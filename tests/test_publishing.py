import asyncio
import struct

import httpx

import pytest

from adapters.linkedin import escape, post_body, post_url, without_trailing_links
from bot.keyboards import parse_publish, publish_keyboard
from core.images import _get_bytes, og_image, page_url, sniff, width


def png(w, h=10):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"


def jpeg(w, h=10):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, h, w) + b"\x00" * 10
    return b"\xff\xd8" + app0 + sof


def test_og_image_absolute_and_twitter_fallback():
    page = '<html><head><meta property="og:image" content="/img/card.png"></head></html>'
    assert og_image(page, "https://blog.dev/post/1") == "https://blog.dev/img/card.png"
    page = '<html><head><meta name="twitter:image" content="https://cdn.dev/x.jpg"></head></html>'
    assert og_image(page, "https://blog.dev/post/1") == "https://cdn.dev/x.jpg"
    assert og_image("<html><head></head></html>", "https://blog.dev/") is None


def test_only_pictures_linkedin_takes_and_their_width():
    assert sniff(png(1200)) == "image/png" and width(png(1200)) == 1200
    assert sniff(jpeg(800)) == "image/jpeg" and width(jpeg(800)) == 800
    assert sniff(b"GIF89a" + struct.pack("<HH", 640, 480)) == "image/gif"
    assert sniff(b"RIFF....WEBPVP8 ") is None  # webp: generated instead


@pytest.mark.parametrize("url, page", [
    ("https://github.com/org/repo", "https://github.com/org/repo"),
    ("https://blog.dev/post/1", "https://blog.dev/post/1"),
    ("https://arxiv.org/abs/2610.14428", None),  # the arXiv logo is not a picture of the paper
    ("https://x.com/a/status/123", None),
])
def test_page_url(url, page):
    assert page_url(url) == page


def test_escape_little_text_format():
    assert escape("Agents (MCP) #llm @you [x] a_b*c~d <e> {f} g|h \\") == \
        "Agents \\(MCP\\) \\#llm \\@you \\[x\\] a\\_b\\*c\\~d \\<e\\> \\{f\\} g\\|h \\\\"


def test_trailing_links_go_under_the_post():
    assert without_trailing_links("Text.\n\nhttps://a.dev/1\nhttps://b.dev/2\n") == "Text."
    assert without_trailing_links("See https://a.dev/1 inside.") == "See https://a.dev/1 inside."


def test_post_body_card_with_picture_or_picture_alone():
    b = post_body("urn:li:person:1", "Hi (v2)\nhttps://a.dev/1", "https://a.dev/1", "Title", "urn:li:image:9")
    assert b["commentary"] == "Hi \\(v2\\)"
    assert b["content"] == {"article": {"source": "https://a.dev/1", "title": "Title", "thumbnail": "urn:li:image:9"}}
    assert b["author"] == "urn:li:person:1" and b["lifecycleState"] == "PUBLISHED" and b["visibility"] == "PUBLIC"
    assert post_body("u", "Hi", None, "T", "urn:li:image:9")["content"] == {"media": {"id": "urn:li:image:9", "altText": "T"}}
    assert "content" not in post_body("u", "Hi", None, "T", None)
    assert post_url("urn:li:share:7") == "https://www.linkedin.com/feed/update/urn:li:share:7/"


def test_publish_button_round_trip():
    data = publish_keyboard(42).inline_keyboard[0][0].callback_data
    assert parse_publish(data) == 42
    assert parse_publish("fb:up:42") is None and parse_publish(None) is None


def test_a_picture_over_the_cap_is_dropped_while_reading():
    big = png(1200) + b"\x00" * 5000

    def handler(request):  # no content-length: the cap must hold on the stream itself
        return httpx.Response(200, stream=_Chunks(big))

    async def get(cap):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await _get_bytes(http, "https://cdn.dev/x.png", cap)

    assert asyncio.run(get(10_000)) == big
    assert asyncio.run(get(1_000)) is None


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data

    async def __aiter__(self):
        for i in range(0, len(self.data), 512):
            yield self.data[i:i + 512]
