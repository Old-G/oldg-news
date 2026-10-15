import asyncio
from datetime import datetime, timezone

import httpx

from adapters.rss import _block_private, html_to_text, is_public_host, parse_feed, parse_hf_papers, parse_hn
from core.normalize import canonical_urls

ARXIV = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>cs.AI updates on arXiv.org</title>
<item><title>Agents That Remember</title><link>https://arxiv.org/abs/2610.14005</link>
<description>arXiv:2610.14005v1 Announce Type: new
Abstract: We study memory for agents.</description>
<guid isPermaLink="false">oai:arXiv.org:2610.14005v1</guid><category>cs.AI</category>
<pubDate>Thu, 15 Oct 2026 00:00:00 -0400</pubDate><arxiv:announce_type>new</arxiv:announce_type>
<dc:creator>A. Author, B. Author</dc:creator></item>
<item><title>Old Paper v3</title><link>https://arxiv.org/abs/2401.00001</link>
<description>arXiv:2401.00001v3 Announce Type: replace
Abstract: Updated.</description>
<guid isPermaLink="false">oai:arXiv.org:2401.00001v3</guid>
<pubDate>Thu, 15 Oct 2026 00:00:00 -0400</pubDate><arxiv:announce_type>replace</arxiv:announce_type></item>
</channel></rss>"""

HN = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Hacker News: Front Page</title>
<item><title>LFM2.5 24B A2B</title>
<description><![CDATA[<p>Article URL: <a href="https://www.liquid.ai/blog/lfm2">https://www.liquid.ai/blog/lfm2</a></p>
<p>Comments URL: <a href="https://news.ycombinator.com/item?id=50097369">x</a></p><p>Points: 200</p><p># Comments: 44</p>]]></description>
<pubDate>Wed, 14 Oct 2026 23:59:37 +0000</pubDate><link>https://www.liquid.ai/blog/lfm2</link>
<comments>https://news.ycombinator.com/item?id=50097369</comments>
<guid isPermaLink="false">https://news.ycombinator.com/item?id=50097369</guid></item>
</channel></rss>"""

BLOG = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title>
<entry><title>Release &amp; notes</title><link href="https://example.com/post"/><id>tag:example.com,1</id>
<updated>2026-10-14T10:00:00Z</updated><content type="html">&lt;p&gt;Hello&lt;br/&gt;world&lt;/p&gt;</content></entry>
</feed>"""


def test_arxiv_keeps_new_skips_replacements():
    (item,) = parse_feed(ARXIV, "https://rss.arxiv.org/rss/cs.AI", "arxiv")
    assert item.url == "https://arxiv.org/abs/2610.14005"
    assert item.text == "We study memory for agents."
    assert item.published_at == datetime(2026, 10, 15, 4, 0, tzinfo=timezone.utc)
    assert item.raw["announce_type"] == "new"


def test_hn_url_is_the_article_and_discussion_goes_to_urls():
    (item,) = parse_hn(HN, "hn")
    assert item.url == "https://www.liquid.ai/blog/lfm2"
    assert item.external_id == "50097369"
    assert item.raw["urls"] == ["https://www.liquid.ai/blog/lfm2", "https://news.ycombinator.com/item?id=50097369"]
    assert (item.raw["points"], item.raw["comments"]) == (200, 44)
    assert item.published_at == datetime(2026, 10, 14, 23, 59, 37, tzinfo=timezone.utc)


def test_atom_blog_entry():
    (item,) = parse_feed(BLOG, "blog", "rss")
    assert (item.title, item.text, item.url) == ("Release & notes", "Hello\nworld", "https://example.com/post")
    assert item.published_at == datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)


def test_hf_papers_point_to_arxiv():
    data = [{"publishedAt": "2026-10-13T20:00:00.000Z", "numComments": 2, "paper": {
        "id": "2610.20137", "title": " StreamErase ", "summary": "Fast erasing.", "upvotes": 12,
        "submittedOnDailyAt": "2026-10-14T06:30:00.000Z", "githubRepo": "https://github.com/a/b"}}]
    (item,) = parse_hf_papers(data, "hf")
    assert item.url == "https://arxiv.org/abs/2610.20137"
    assert item.title == "StreamErase"
    assert item.published_at == datetime(2026, 10, 14, 6, 30, tzinfo=timezone.utc)
    assert item.raw["upvotes"] == 12
    assert "https://github.com/a/b" in item.raw["urls"]


def test_html_to_text():
    assert html_to_text("<p>a &amp; b</p><p>c</p>") == "a & b\nc"


def test_private_addresses_are_blocked():
    async def run():
        return {h: await is_public_host(h) for h in
                ["127.0.0.1", "10.0.0.5", "172.18.0.2", "192.168.1.1", "169.254.169.254", "::1", "localhost", "8.8.8.8"]}

    assert asyncio.run(run()) == {"127.0.0.1": False, "10.0.0.5": False, "172.18.0.2": False, "192.168.1.1": False,
                                  "169.254.169.254": False, "::1": False, "localhost": False, "8.8.8.8": True}


def test_redirect_to_private_address_keeps_short_link():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "http://169.254.169.254/latest/meta-data"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), event_hooks={"request": [_block_private]}) as c:
            return await canonical_urls(["https://bit.ly/evil"], c)

    assert asyncio.run(run()) == ["https://bit.ly/evil"]
