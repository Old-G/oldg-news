"""page_watch: news pages without a feed (Anthropic, Meta AI, Cohere) → RawItem (spec: «хэш страницы + diff → item»).

The page's state is the set of article links on it; the diff against the links seen before gives the new
articles. An article link is one level under the page's own path, on the same host: anthropic.com/news →
anthropic.com/news/<slug>. Each new article is fetched once for its title, date and text.

The first run only records what is already there: a page lists dozens of old posts that are not news.
The seen links live in sources.last_external_id as a JSON list.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import httpx
import lxml.html
import trafilatura

from adapters import RawItem
from core.normalize import canonical_url

log = logging.getLogger(__name__)
SKIP_SEGMENTS = {"tag", "tags", "category", "categories", "page", "author", "authors", "rss", "feed", "topic",
                 "topics", "search", "archive"}
MAX_SEEN = 500
TEXT_CHARS = 6000


def article_links(html: str, page_url: str) -> list[str]:
    """Canonical article URLs on the page, in page order."""
    base = urlsplit(page_url)
    prefix = base.path.rstrip("/") + "/"
    out = []
    for a in lxml.html.fromstring(html).iter("a"):
        u = urlsplit(urljoin(page_url, a.get("href") or ""))
        if u.hostname != base.hostname or not u.path.startswith(prefix):
            continue
        slug = u.path[len(prefix):].strip("/")
        if not slug or "/" in slug or slug.lower() in SKIP_SEGMENTS:
            continue
        out.append(canonical_url(f"{u.scheme}://{u.hostname}{prefix}{slug}"))
    return list(dict.fromkeys(out))


def _meta(doc, *names: str) -> str | None:
    for n in names:
        v = doc.xpath(f'//meta[@property="{n}" or @name="{n}"]/@content')
        if v and v[0].strip():
            return " ".join(v[0].split())
    return None


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_article(html: str, url: str) -> tuple[str | None, datetime | None, str | None]:
    """(title, published_at, text): og:title / <title>; article:published_time, JSON-LD datePublished or <time>;
    the main text (trafilatura), else the description."""
    doc = lxml.html.fromstring(html)
    title = _meta(doc, "og:title", "twitter:title") or " ".join(" ".join(doc.xpath("//title//text()")).split()) or None
    m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    published = (_date(_meta(doc, "article:published_time", "datePublished", "date"))
                 or _date(m.group(1) if m else None)
                 or next(filter(None, (_date(t) for t in doc.xpath("//time/@datetime"))), None))
    text = trafilatura.extract(html, url=url, include_comments=False, favor_precision=True)
    text = (text or _meta(doc, "og:description", "description") or "")[:TEXT_CHARS] or None
    return title, published, text


async def fetch(http: httpx.AsyncClient, page_url: str, cursor: str | None, max_age_h: int,
                source_key: str) -> tuple[list[RawItem], str, dict]:
    """(new items, new cursor, stats). Articles dated older than max_age_h are recorded as seen, not emitted."""
    r = await http.get(page_url)
    r.raise_for_status()
    links = article_links(r.text, str(r.url))
    stats = {"links": len(links), "new": 0, "too_old": 0, "failed": 0, "baseline": cursor is None}
    if not links:
        raise ValueError(f"no article links on {page_url}: the page layout changed?")
    seen = json.loads(cursor) if cursor else []
    if cursor is None:
        return [], json.dumps(links[:MAX_SEEN]), stats
    known = set(seen)
    items = []
    since = datetime.now(timezone.utc) - timedelta(hours=max_age_h)
    for url in [u for u in links if u not in known]:
        try:
            a = await http.get(url)
            a.raise_for_status()
            title, published, text = parse_article(a.text, url)
        except Exception as e:
            log.warning("page_watch article %s failed: %r", url, e)
            stats["failed"] += 1
            continue  # not marked seen: tried again next run
        seen.insert(0, url)
        if published and published < since:
            stats["too_old"] += 1
            continue
        stats["new"] += 1
        items.append(RawItem(source_key, url, url, title, text, published or datetime.now(timezone.utc),
                             {"urls": [url], "via": "page_watch", "page": page_url}))
    return items, json.dumps(list(dict.fromkeys(seen))[:MAX_SEEN]), stats
