"""👍 / 👎 / ⭐ under digest posts. Anyone can press them in the channel: the owner's presses train
the taste (origin bot), everyone else's are an audience signal (origin audience) and never rank."""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.keyboards import feedback_keyboard, parse
from config import get_settings
from core.digest import owner_may
from db import queries

log = logging.getLogger(__name__)
STATUS_AFTER = {"down": "dismissed", "star": "starred"}


def build_router(owner_id: int) -> Router:
    router = Router(name="feedback")

    @router.callback_query(F.data.startswith("fb:"))
    async def on_press(cb: CallbackQuery) -> None:
        parsed = parse(cb.data)
        if not parsed or parsed[0] == "noop":
            await cb.answer()
            return
        action, story_id = parsed
        with queries.connect(get_settings().database_url) as conn:
            digest_id = queries.latest_digest_of(conn, story_id)
            if cb.from_user.id != owner_id:
                if action == "star":
                    await cb.answer("⭐ — это кнопка автора канала")
                    return
                queries.add_feedback(conn, story_id, action, "audience", digest_id)
                await cb.answer("Спасибо!")
                return
            done = queries.owner_actions(conn, story_id)
            if not owner_may(action, done):
                await cb.answer()
                return
            queries.add_feedback(conn, story_id, action, "bot", digest_id)
            if action in STATUS_AFTER:
                queries.set_story_status(conn, story_id, STATUS_AFTER[action])
            done.add(action)
        # the mark replaces the buttons, so a second press is impossible (spec: «✓ 👍»)
        await cb.message.edit_reply_markup(reply_markup=feedback_keyboard(story_id, done))
        await cb.answer({"up": "👍 записал", "down": "👎 записал", "star": "⭐ записал, черновики — на шаге 7"}[action])

    return router
