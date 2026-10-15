"""/add <url>: a manual item (source = manual). It joins the digest out of turn from step 5 on."""

import html
import logging
import re
from datetime import datetime, timezone

import httpx
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from adapters import RawItem, rss
from config import get_settings
from core import normalize
from db import queries

log = logging.getLogger(__name__)
MAX_PAGE_BYTES = 1_000_000


def _meta(page: str, *names: str) -> str | None:
    for name in names:
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{name}["\'][^>]+content=["\']([^"\']+)', page, re.I) or \
            re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{name}["\']', page, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return None


async def describe(http: httpx.AsyncClient, url: str) -> tuple[str | None, str | None]:
    """Title and short text of a page; X posts go through fxtwitter. Never raises."""
    try:
        if m := re.match(r"https://x\.com/(\w+)/status/(\d+)", url):
            r = await http.get(f"https://api.fxtwitter.com/{m.group(1)}/status/{m.group(2)}")
            t = r.json()["tweet"]
            return f"@{t['author']['screen_name']}: {t['text'][:80]}", t["text"]
        async with http.stream("GET", url) as r:
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > MAX_PAGE_BYTES:
                    break
        page = body.decode(r.encoding or "utf-8", errors="replace")
        title = _meta(page, "og:title", "twitter:title")
        if not title and (m := re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)):
            title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
        return title or None, _meta(page, "og:description", "description", "twitter:description")
    except Exception as e:
        log.warning("describe %s failed: %r", url, e)
        return None, None


def build_router(owner_id: int) -> Router:
    router = Router(name="add")
    router.message.filter(F.chat.id == owner_id)  # anyone else is ignored silently

    @router.message(Command("add"))
    async def add(message: Message, command: CommandObject) -> None:
        url = (command.args or "").split()[0] if command.args else ""
        if not url:
            await message.answer("Формат: /add &lt;url&gt;")
            return
        s = get_settings()
        async with rss.make_http_client(s.http_timeout_s) as http:
            url = (await normalize.canonical_urls([url], http, s.short_link_timeout_s))[0]
            title, text = await describe(http, url)
        item = RawItem("manual", url, url, title or url, text, datetime.now(timezone.utc), {"urls": [url], "via": "bot /add"})
        with queries.connect(s.database_url) as conn:
            src = queries.upsert_source(conn, "manual", "manual", "Manual /add")
            item_id, created = queries.insert_manual_item(conn, src.id, item)
        verb = "Добавил" if created else "Уже есть"
        await message.answer(f"{verb} #{item_id}: <b>{html.escape(item.title)}</b>\n{html.escape(url)}")

    return router
