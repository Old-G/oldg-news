"""The picture for a ⭐ story's posts (spec step 12): the main link's own og:image when it is a real picture,
otherwise one drawn by IMAGE_MODEL from prompt `image` in the channel's style."""

import base64
import logging
import struct
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html
from openai import AsyncOpenAI

from config import Settings
from core.article import _get_text, primary_kind
from core.llm import Usage, load_prompt, price, render

log = logging.getLogger(__name__)
MIMES = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"GIF8": "image/gif"}  # what LinkedIn takes


@dataclass
class Picture:
    kind: str  # page | generated
    source_url: str | None
    mime: str
    data: bytes


def og_image(page: str, base_url: str) -> str | None:
    """og:image (or twitter:image) of a page, made absolute; None when the page has none."""
    try:
        doc = lxml_html.fromstring(page)
    except Exception:
        return None
    for key in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image"):
        for el in doc.xpath(f'//meta[@property="{key}" or @name="{key}"]'):
            if url := (el.get("content") or "").strip():
                return urljoin(base_url, url)
    return None


def sniff(data: bytes) -> str | None:
    """The mime type by the file's first bytes; None for anything but PNG, JPEG and GIF."""
    return next((m for magic, m in MIMES.items() if data.startswith(magic)), None)


def width(data: bytes) -> int | None:
    """Pixel width from the PNG, GIF or JPEG header; None when it cannot be read."""
    if data.startswith(b"\x89PNG") and len(data) >= 24:
        return struct.unpack(">I", data[16:20])[0]
    if data.startswith(b"GIF8") and len(data) >= 10:
        return struct.unpack("<H", data[6:8])[0]
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                return None
            marker, size = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):  # start of frame: height, then width
                return struct.unpack(">H", data[i + 7:i + 9])[0]
            i += 2 + size
    return None


def page_url(url: str) -> str | None:
    """The page whose og:image stands for the link; None where there is no picture of the thing itself
    (an arXiv page shows the arXiv logo, an X post has no page of its own)."""
    match primary_kind(url):
        case "github", repo:
            return f"https://github.com/{repo}"
        case "page", page:
            return page
    return None


async def from_page(http: httpx.AsyncClient, s: Settings, url: str) -> Picture | None:
    """The main link's og:image when it is a PNG/JPEG/GIF picture at least IMAGE_MIN_WIDTH wide. Never raises."""
    page = page_url(url)
    if not page:
        return None
    try:
        img_url = og_image(await _get_text(http, page), page)
        if not img_url:
            return None
        r = await http.get(img_url)
        r.raise_for_status()
        data = r.content
        mime = sniff(data)
        if not mime or len(data) > s.image_max_bytes or (width(data) or 0) < s.image_min_width:
            log.info("og:image of %s skipped: %s, %s bytes, width %s", url, mime, len(data), width(data))
            return None
        return Picture("page", img_url, mime, data)
    except Exception as e:
        log.warning("og:image of %s failed: %r", url, e)
        return None


async def generate(client: AsyncOpenAI, s: Settings, usage: Usage, title: str, summary: str) -> Picture:
    p = load_prompt("image")
    r = await client.images.generate(model=s.image_model, prompt=f"{p.system}\n\n{render(p.user, title=title, summary=summary)}",
                                     size="1536x1024", quality=s.image_quality, n=1)
    if r.usage:
        p_in, p_out = price(s, s.image_model)
        usage.input_tokens += r.usage.input_tokens
        usage.output_tokens += r.usage.output_tokens
        usage.usd += (r.usage.input_tokens * p_in + r.usage.output_tokens * p_out) / 1_000_000
    usage.calls += 1
    usage.by_model[s.image_model] = usage.by_model.get(s.image_model, 0) + 1
    data = base64.b64decode(r.data[0].b64_json)
    return Picture("generated", None, sniff(data) or "image/png", data)
