"""Feed adapters: RSS/Atom (blogs, arXiv, GitHub releases, Medium, Reddit), HF daily papers, Hacker News.

Parsing is pure (bytes / JSON → RawItem) so it is tested without the network.
"""

import calendar
import html
import re
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx

from adapters import RawItem

USER_AGENT = "radar/0.1 (personal AI news digest; +https://t.me/oldgnews)"
ARXIV_SKIP = {"replace", "replace-cross"}  # new versions of old papers, not news


def html_to_text(s: str | None) -> str:
    s = re.sub(r"<(br|/p|/li|/h\d)\s*/?>", "\n", s or "", flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in s.splitlines())
    return "\n".join(line for line in lines if line)


def _entry_date(e: Any) -> datetime | None:
    t = e.get("published_parsed") or e.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(t), timezone.utc) if t else None


def parse_feed(content: bytes, source_key: str, kind: str) -> list[RawItem]:
    feed = feedparser.parse(content)
    items = []
    for e in feed.entries:
        if kind == "arxiv" and e.get("arxiv_announce_type") in ARXIV_SKIP:
            continue
        link = e.get("link")
        text = html_to_text(e.get("summary") or (e.get("content") or [{}])[0].get("value"))
        if kind == "arxiv":
            text = re.sub(r"^arXiv:\S+\s+Announce Type:\s*\S+\s+Abstract:\s*", "", text)
        items.append(
            RawItem(
                source_key=source_key,
                external_id=e.get("id") or link,
                url=link,
                title=html_to_text(e.get("title")) or None,
                text=text or None,
                published_at=_entry_date(e),
                raw={
                    "urls": [link] if link else [],
                    "feed": feed.feed.get("title"),
                    "authors": [a.get("name") for a in e.get("authors", []) if a.get("name")],
                    "tags": [t.get("term") for t in e.get("tags", []) if t.get("term")],
                    **({"announce_type": e.get("arxiv_announce_type")} if kind == "arxiv" else {}),
                },
            )
        )
    return items


def parse_hn(content: bytes, source_key: str) -> list[RawItem]:
    """hnrss: url is the external link, the discussion goes to urls."""
    items = []
    for e in feedparser.parse(content).entries:
        comments = e.get("comments")
        link = e.get("link") or comments
        summary = e.get("summary", "")
        points = re.search(r"Points:\s*(\d+)", summary)
        n_comments = re.search(r"# Comments:\s*(\d+)", summary)
        hn_id = re.search(r"id=(\d+)", comments or e.get("id", ""))
        items.append(
            RawItem(
                source_key=source_key,
                external_id=hn_id.group(1) if hn_id else e.get("id"),
                url=link,
                title=html_to_text(e.get("title")),
                text=html_to_text(e.get("title")),
                published_at=_entry_date(e),
                raw={
                    "urls": list(dict.fromkeys(u for u in (link, comments) if u)),
                    "points": int(points.group(1)) if points else None,
                    "comments": int(n_comments.group(1)) if n_comments else None,
                    "discussion": comments,
                },
            )
        )
    return items


def parse_hf_papers(data: list[dict], source_key: str) -> list[RawItem]:
    """HF daily papers; upvotes go to raw and do not affect the score in v1."""
    items = []
    for d in data:
        p = d.get("paper", {})
        pid = p.get("id")
        if not pid:
            continue
        added = p.get("submittedOnDailyAt") or d.get("publishedAt") or p.get("publishedAt")
        urls = [f"https://arxiv.org/abs/{pid}", f"https://huggingface.co/papers/{pid}", p.get("githubRepo"), p.get("projectPage")]
        items.append(
            RawItem(
                source_key=source_key,
                external_id=pid,
                url=f"https://arxiv.org/abs/{pid}",
                title=(p.get("title") or d.get("title") or "").strip() or None,
                text=(p.get("summary") or d.get("summary") or "").strip() or None,
                published_at=datetime.fromisoformat(added.replace("Z", "+00:00")) if added else None,
                raw={
                    "urls": [u for u in urls if u],
                    "upvotes": p.get("upvotes"),
                    "comments": d.get("numComments"),
                    "github": p.get("githubRepo"),
                    "published_on_arxiv": p.get("publishedAt"),
                },
            )
        )
    return items


async def fetch(client: httpx.AsyncClient, kind: str, url: str, source_key: str) -> list[RawItem]:
    r = await client.get(url)
    r.raise_for_status()
    if kind == "hf_papers":
        return parse_hf_papers(r.json(), source_key)
    if kind == "hn":
        return parse_hn(r.content, source_key)
    return parse_feed(r.content, source_key, kind)


def make_http_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
