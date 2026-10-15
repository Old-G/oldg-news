import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx

from adapters.page_watch import article_links, fetch, parse_article

PAGE = """<html><body>
<a href="/news">News</a>
<a href="/news/claude-for-x">Claude for X</a>
<a href="https://www.example.ai/news/claude-for-x/">Same, absolute with a slash</a>
<a href="/news/tag/policy">Tag page</a>
<a href="/news/rss">Feed</a>
<a href="/news/a/b">Too deep</a>
<a href="/research/paper-1">Other section</a>
<a href="https://other.example/news/x">Other host</a>
<a href="/news/scion?utm_source=x">Scion</a>
</body></html>"""


def test_article_links_one_level_under_the_page_same_host():
    assert article_links(PAGE, "https://www.example.ai/news") == [
        "https://example.ai/news/claude-for-x", "https://example.ai/news/scion"]


ARTICLE = """<html><head>
<meta property="og:title" content="Redesigning source control">
<meta property="article:published_time" content="2026-10-13T00:00:00.000Z">
<meta name="description" content="Short description">
</head><body><time datetime="2020-01-01"></time></body></html>"""


def test_parse_article_prefers_meta_then_json_ld_then_time():
    title, published, text = parse_article(ARTICLE, "https://example.ai/news/scion")
    assert (title, published, text) == ("Redesigning source control", datetime(2026, 10, 13, tzinfo=timezone.utc),
                                         "Short description")
    ld = '<html><head><title> Embed 5 </title><script type="application/ld+json">{"datePublished": "2026-09-30T09:51:00.000Z"}</script></head></html>'
    assert parse_article(ld, "https://c.com/blog/embed-5")[:2] == ("Embed 5", datetime(2026, 9, 30, 9, 51, tzinfo=timezone.utc))
    assert parse_article("<html><title>T</title><time datetime='2026-10-01'></time></html>", "u")[1] == \
        datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert parse_article("<html><title>T</title></html>", "u")[1] is None


def run_fetch(pages: dict[str, str], cursor):
    def handler(req: httpx.Request) -> httpx.Response:
        body = pages.get(str(req.url))
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await fetch(http, "https://example.ai/news", cursor, 48, "src")
    return asyncio.run(go())


def art(title: str, published: datetime | None) -> str:
    date = f'<meta property="article:published_time" content="{published.isoformat()}">' if published else ""
    return f'<html><head><meta property="og:title" content="{title}">{date}</head><body><p>{title} body</p></body></html>'


def test_first_run_is_a_baseline_then_only_new_fresh_articles():
    now = datetime.now(timezone.utc)
    page1 = '<a href="/news/old-1">o</a><a href="/news/old-2">o</a>'
    items, cursor, stats = run_fetch({"https://example.ai/news": page1}, None)
    assert items == [] and stats["baseline"] and json.loads(cursor) == ["https://example.ai/news/old-1", "https://example.ai/news/old-2"]

    page2 = '<a href="/news/fresh">f</a><a href="/news/stale">s</a><a href="/news/broken">b</a>' + page1
    items, cursor2, stats = run_fetch({
        "https://example.ai/news": page2,
        "https://example.ai/news/fresh": art("Fresh", now - timedelta(hours=2)),
        "https://example.ai/news/stale": art("Stale", now - timedelta(days=30)),
    }, cursor)
    assert [(i.title, i.url) for i in items] == [("Fresh", "https://example.ai/news/fresh")]
    assert (stats["new"], stats["too_old"], stats["failed"]) == (1, 1, 1)
    seen = json.loads(cursor2)
    assert "https://example.ai/news/stale" in seen and "https://example.ai/news/broken" not in seen  # retried later

    items, _, stats = run_fetch({"https://example.ai/news": page2,
                                 "https://example.ai/news/broken": art("Undated", None)}, cursor2)
    assert [i.title for i in items] == ["Undated"] and items[0].published_at > now - timedelta(minutes=1)
