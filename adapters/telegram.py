"""Telegram adapter: every broadcast channel the user account follows, read through Telethon."""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from telethon import TelegramClient, utils
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.custom import Dialog
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

from adapters import RawItem

log = logging.getLogger(__name__)


def make_client(api_id: int, api_hash: str, session: str = "") -> TelegramClient:
    # flood_sleep_threshold=0: every FloodWait surfaces to us, so we sleep e.seconds + 5 and count it.
    # receive_updates=False: we read history in batches; catching up channel updates
    # (GetChannelDifference per channel) made a run ~20x slower on the shared connection.
    return TelegramClient(
        StringSession(session), api_id, api_hash,
        flood_sleep_threshold=0, receive_updates=False, device_model="Radar",
    )


async def with_floodwait[T](call: Callable[[], Awaitable[T]], retries: int, stats: dict) -> T:
    for attempt in range(retries + 1):
        try:
            return await call()
        except FloodWaitError as e:
            if attempt == retries:
                raise
            log.warning("FloodWait %ss, sleeping", e.seconds)
            stats["floodwait_s"] = stats.get("floodwait_s", 0) + e.seconds + 5
            await asyncio.sleep(e.seconds + 5)
    raise AssertionError("unreachable")


async def list_channels(client: TelegramClient, exclude_ids: set[int]) -> list[Dialog]:
    """Broadcast channels only: groups and megagroups are not sources."""
    return [
        d
        async for d in client.iter_dialogs()
        if d.is_channel and not d.is_group and utils.get_peer_id(d.entity) not in exclude_ids
    ]


async def fetch_messages(
    client: TelegramClient, entity: Any, min_id: int | None, backfill_hours: int
) -> tuple[list[Any], int | None]:
    """Messages newer than min_id; without a cursor, the last backfill_hours.

    Also returns the newest message id seen — even when that message is older than the
    backfill window — so the caller stores it as the cursor and never rescans a quiet channel.
    """
    if min_id:
        messages = [m async for m in client.iter_messages(entity, min_id=min_id)]
        return messages, max((m.id for m in messages), default=None)
    since = datetime.now(timezone.utc) - timedelta(hours=backfill_hours)
    messages, newest_id = [], None
    async for m in client.iter_messages(entity):
        newest_id = newest_id or m.id
        if m.date < since:
            break
        messages.append(m)
    return messages, newest_id


def _utf16_slice(text: str, offset: int, length: int) -> str:
    # Telegram entity offsets count UTF-16 code units, not Python characters
    b = text.encode("utf-16-le")
    return b[offset * 2 : (offset + length) * 2].decode("utf-16-le")


def extract_urls(message: Any) -> list[str]:
    """Links in order of appearance: hidden text links, plain URLs, then the web preview."""
    text = message.message or ""
    urls: list[str] = []
    for e in message.entities or []:
        if isinstance(e, MessageEntityTextUrl):
            url = e.url
        elif isinstance(e, MessageEntityUrl):
            url = _utf16_slice(text, e.offset, e.length)
            if "://" not in url:
                url = "https://" + url
        else:
            continue
        if url not in urls:
            urls.append(url)
    preview = getattr(getattr(message.media, "webpage", None), "url", None)
    if preview and preview not in urls:
        urls.append(preview)
    return urls


def _message_text(message: Any) -> str:
    """message.message plus hidden links, which in RU channels carry most of the URLs."""
    text = (message.message or "").strip()
    hidden = [e.url for e in message.entities or [] if isinstance(e, MessageEntityTextUrl)]
    hidden = [u for u in dict.fromkeys(hidden) if u not in text]
    return "\n".join([text, *hidden]) if text and hidden else text


def _preview(message: Any) -> dict | None:
    """Telegram's link preview: the only description a bare-link post carries."""
    wp = getattr(message.media, "webpage", None)
    title, description = getattr(wp, "title", None), getattr(wp, "description", None)
    if not (title or description):
        return None
    return {"title": title, "description": description, "site": getattr(wp, "site_name", None)}


def _fwd_channel_id(message: Any) -> int | None:
    fwd = message.fwd_from
    return getattr(fwd.from_id, "channel_id", None) if fwd and fwd.from_id else None


def messages_to_items(
    messages: Iterable[Any], channel_id: int, username: str | None
) -> tuple[list[RawItem], int]:
    """Albums (one grouped_id) become one item; posts without text are skipped.

    Returns the items and the number of skipped text-less posts.
    """
    groups: dict[Any, list[Any]] = {}
    for m in messages:
        groups.setdefault(m.grouped_id or ("single", m.id), []).append(m)

    items, skipped = [], 0
    for parts in groups.values():
        parts.sort(key=lambda m: m.id)
        text = "\n\n".join(t for t in (_message_text(m) for m in parts) if t)
        if not text:
            skipped += 1
            continue
        urls = list(dict.fromkeys(u for m in parts for u in extract_urls(m)))
        preview = next((p for p in map(_preview, parts) if p), None)
        title = text.splitlines()[0][:200]
        if preview and len(re.sub(r"https?://\S+", "", text).strip()) < 100:
            # a bare link: the preview is what the post is about
            title = (preview["title"] or title)[:200]
            text = "\n".join(t for t in (text, preview["title"], preview["description"]) if t)
        head = parts[0]
        fwd = next((m for m in parts if m.fwd_from), None)
        items.append(
            RawItem(
                source_key=str(channel_id),
                external_id=str(head.id),
                url=urls[0] if urls else None,
                title=title,
                text=text,
                published_at=head.date,
                raw={
                    "channel_id": channel_id,
                    "message_ids": [m.id for m in parts],
                    "grouped_id": head.grouped_id,
                    "link": f"https://t.me/{username}/{head.id}" if username else None,
                    "urls": urls,
                    "fwd_from_channel_id": _fwd_channel_id(fwd) if fwd else None,
                    "fwd_from_post_id": fwd.fwd_from.channel_post if fwd else None,
                    "views": max((m.views or 0 for m in parts), default=0),
                    "forwards": max((m.forwards or 0 for m in parts), default=0),
                    "has_media": any(m.media for m in parts),
                    "preview": preview,
                },
            )
        )
    return items, skipped
