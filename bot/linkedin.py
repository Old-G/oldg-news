"""LinkedIn in the owner's DM (spec: «Бот»): /linkedin <token> connects, «Опубликовать в LinkedIn» under a draft
publishes it, a reply to the draft with new text replaces what gets published."""

import logging
from datetime import datetime, timezone

import httpx
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from adapters.linkedin import LinkedIn, LinkedInError, post_body, post_url
from bot.keyboards import parse_publish, publish_keyboard, published_keyboard
from config import Settings, get_settings
from core import digest
from core.normalize import is_specific_url
from db import queries

log = logging.getLogger(__name__)
_publishing: set[int] = set()  # stories being published right now: a double press posts once


def _expires(token: dict) -> str:
    return token["expires_at"].astimezone().strftime("%d.%m.%Y")


def _live(token: dict | None) -> bool:
    return bool(token) and token["expires_at"] > datetime.now(timezone.utc)


async def publish(s: Settings, conn, story_id: int) -> str:
    """Publishes the story's LinkedIn draft (your edit if any) with the picture; the post's url."""
    draft = queries.linkedin_draft(conn, story_id)
    if not draft:
        raise LinkedInError("у этой истории нет LinkedIn-черновика")
    if draft["published_urn"]:
        return post_url(draft["published_urn"])
    token = queries.oauth_token(conn, "linkedin")
    card = queries.story_card(conn, story_id)
    link = next((u for u in digest.story_links(card, is_specific_url) if is_specific_url(u) and "t.me/" not in u), None)
    pic = queries.story_image(conn, story_id)
    async with httpx.AsyncClient(timeout=s.http_timeout_s) as http:
        li = LinkedIn(http, token["token"], s.linkedin_version)
        image_urn = await li.upload_image(token["subject"], pic[3]) if pic else None
        urn = await li.publish(post_body(token["subject"], draft["text"], link, card["title"] or "", image_urn))
    queries.set_draft_published(conn, draft["id"], urn)
    conn.commit()
    return post_url(urn)


def build_router(owner_id: int) -> Router:
    router = Router(name="linkedin")
    router.message.filter(F.chat.id == owner_id)

    @router.message(Command("linkedin"))
    async def on_linkedin(message: Message, command: CommandObject) -> None:
        s = get_settings()
        with queries.connect(s.database_url) as conn:
            if not command.args:
                token = queries.oauth_token(conn, "linkedin")
                await message.answer(f"LinkedIn подключён до {_expires(token)}." if _live(token) else
                                     "LinkedIn не подключён. Пришли /linkedin <токен> из Developer Portal → OAuth Token Tools.")
                return
            try:
                await message.delete()  # the token must not stay in the chat
            except Exception:
                log.warning("could not delete the message with the token")
            try:
                async with httpx.AsyncClient(timeout=s.http_timeout_s) as http:
                    subject, name = await LinkedIn(http, command.args.strip(), s.linkedin_version).whoami()
            except Exception as e:
                log.warning("linkedin token check failed: %r", e)
                await message.answer("Токен не подошёл: LinkedIn его не принял. Нужны scopes openid, profile, w_member_social.")
                return
            queries.save_oauth_token(conn, "linkedin", command.args.strip(), subject, s.linkedin_token_days)
            conn.commit()
            token = queries.oauth_token(conn, "linkedin")
        await message.answer(f"LinkedIn подключён: {name}, до {_expires(token)}. Сообщение с токеном удалил.")

    @router.callback_query(F.data.startswith("li:"))
    async def on_publish(cb: CallbackQuery) -> None:
        story_id = parse_publish(cb.data)
        if cb.from_user.id != owner_id or story_id is None:
            await cb.answer()
            return
        s = get_settings()
        with queries.connect(s.database_url) as conn:
            if not _live(queries.oauth_token(conn, "linkedin")):
                await cb.answer("LinkedIn не подключён или доступ истёк: пришли /linkedin <токен>", show_alert=True)
                return
            if story_id in _publishing:
                await cb.answer("Уже публикую…")
                return
            _publishing.add(story_id)
            try:
                url = await publish(s, conn, story_id)
            except Exception as e:
                log.exception("linkedin publish for story %s failed", story_id)
                await cb.answer(f"Не опубликовалось: {str(e)[:150]}", show_alert=True)
                return
            finally:
                _publishing.discard(story_id)
        await cb.message.edit_reply_markup(reply_markup=published_keyboard(url))
        await cb.answer("Опубликовано в LinkedIn")

    @router.message(F.reply_to_message.reply_markup, F.text, ~F.text.startswith("/"))
    async def on_edit(message: Message) -> None:
        """A reply to a LinkedIn draft with new text: that text is what the button publishes."""
        rows = message.reply_to_message.reply_markup.inline_keyboard
        story_id = next((parse_publish(b.callback_data) for row in rows for b in row if parse_publish(b.callback_data)), None)
        if story_id is None:
            return
        with queries.connect(get_settings().database_url) as conn:
            draft = queries.linkedin_draft(conn, story_id)
            if not draft or draft["published_urn"]:
                await message.answer("Этот пост уже опубликован — поправить его можно в самом LinkedIn.")
                return
            queries.set_draft_edit(conn, draft["id"], message.text)
            conn.commit()
        await message.answer("Правку сохранил — кнопка опубликует этот текст:", reply_markup=publish_keyboard(story_id))

    return router
