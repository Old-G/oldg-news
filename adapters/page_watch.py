"""page_watch: news pages without a feed (Anthropic, Meta AI, Cohere) → RawItem (spec: «хэш страницы + diff → item»).

The page's state is the set of article links on it; the diff against the links seen before gives the new
articles. An article link is one level under the page's own path, on the same host: anthropic.com/news →
anthropic.com/news/<slug>. Each new article is fetched once for its title, date and text.

The first run only records what is already there: a page lists dozens of old posts that are not news.
The seen links live in sources.last_external_id as a JSON list.

Release notes (a source URL ending in #sections) are one long page instead: its state is the set of dated
sections («October 14, 2026» headings with an id), and each new section is an item of its own, with the
heading's anchor in its URL so the sections of one page stay apart.
"""

import json
import logging
import re
from collections import Counter
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
SECTIONS = "#sections"
_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y")
_BLOCKS = {"p", "li", "pre", "h4", "h5"}


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


# --- release notes: dated sections of one page -----------------------------------

def _heading(text: str) -> str:
    return " ".join(re.sub(r"[\ue000-\uf8ff]", " ", text).split())  # icon-font glyphs next to the date


def heading_date(text: str) -> datetime | None:
    clean = _heading(text)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(clean, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def sections(html: str) -> list[tuple[str, str, datetime, str]]:
    """(anchor id, heading, date, text) of each dated section, in page order. Dated headings with an id are
    found at the level most of them use (h2 or h3); a section's text is the paragraphs and list items up to
    the next one."""
    doc = lxml.html.fromstring(html)
    dated = [e for e in doc.iter("h2", "h3") if e.get("id") and heading_date(e.text_content())]
    if not dated:
        return []
    level = Counter(e.tag for e in dated).most_common(1)[0][0]
    marks = {e for e in dated if e.tag == level}
    out: list[list] = []
    for el in doc.iter():
        if el in marks:
            out.append([el.get("id"), _heading(el.text_content()), heading_date(el.text_content()), []])
        elif out and el.tag in _BLOCKS and not any(a.tag in _BLOCKS for a in el.iterancestors()):
            if text := " ".join(" ".join(el.itertext()).split()):  # nested items keep a space between them
                out[-1][3].append(("• " if el.tag == "li" else "") + text)
    return [(a, h, d, "\n".join(t)[:TEXT_CHARS]) for a, h, d, t in out]


async def fetch_sections(http: httpx.AsyncClient, source_url: str, cursor: str | None, max_age_h: int,
                         source_key: str, name: str) -> tuple[list[RawItem], str, dict]:
    """(new items, new cursor, stats) for a release-notes page; the cursor holds the seen anchors."""
    page_url = canonical_url(source_url.removesuffix(SECTIONS))
    r = await http.get(source_url.removesuffix(SECTIONS))
    r.raise_for_status()
    found = sections(r.text)
    stats = {"sections": len(found), "new": 0, "too_old": 0, "baseline": cursor is None}
    if not found:
        raise ValueError(f"no dated sections on {page_url}: the page layout changed?")
    if cursor is None:
        return [], json.dumps([a for a, *_ in found][:MAX_SEEN]), stats
    seen = json.loads(cursor)
    known, since, items = set(seen), datetime.now(timezone.utc) - timedelta(hours=max_age_h), []
    for anchor, heading, date, text in found:
        if anchor in known:
            continue
        seen.insert(0, anchor)
        if date < since:
            stats["too_old"] += 1
            continue
        stats["new"] += 1
        url = f"{page_url}#{anchor}"
        items.append(RawItem(source_key, url, url, f"{name}: {heading}", text or None, date,
                             {"urls": [url], "via": "page_watch sections", "page": page_url}))
    return items, json.dumps(list(dict.fromkeys(seen))[:MAX_SEEN]), stats
