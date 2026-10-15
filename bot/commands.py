"""/today and /stats in the owner's DM."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from config import get_settings
from db import queries
from jobs.daily import NO_PREVIEW, send_entry, today, today_overview


def build_router(owner_id: int) -> Router:
    router = Router(name="commands")
    router.message.filter(F.chat.id == owner_id)

    @router.message(Command("today"))
    async def on_today(message: Message) -> None:
        """What the channel got today: the overview and the posts already sent."""
        s = get_settings()
        with queries.connect(s.database_url) as conn:
            found = queries.digest_for(conn, today(s))
            if not found or found[1] is None:
                await message.answer("Сегодня в канале ещё ничего не было.")
                return
            entries = queries.digest_entries(conn, found[0])
            sent = [e for e in entries if e[3] is not None]
            await message.answer(f"Сегодня: {len(entries)} историй, в канале уже {len(sent)}.")
            await message.answer(today_overview(conn, s, found[0]), link_preview_options=NO_PREVIEW)
            for story_id, position, _, _ in sent:
                await send_entry(message.bot, conn, message.chat.id, story_id, position)

    @router.message(Command("stats"))
    async def on_stats(message: Message) -> None:
        with queries.connect(get_settings().database_url) as conn:
            st = queries.week_stats(conn)
        await message.answer(
            "За 7 дней\n"
            f"историй: {st['stories']}, в дайджесте: {st['in_digest']}\n"
            f"👍 {st['up']} · 👎 {st['down']} · ⭐ {st['star']} · missed {st['missed']}\n"
            f"нажатия подписчиков: {st['audience']}\n"
            f"LLM и эмбеддинги: ${float(st['usd']):.2f}"
        )

    return router
