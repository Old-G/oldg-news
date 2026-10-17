"""Inline buttons under digest posts; shared by the worker (sending) and the bot (callbacks)."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.digest import buttons


def feedback_keyboard(story_id: int, owner_actions: set[str] = frozenset()) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f"fb:{action}:{story_id}")
        for label, action in buttons(set(owner_actions))
    ]])


def parse(data: str | None) -> tuple[str, int] | None:
    parts = (data or "").split(":")
    if len(parts) == 3 and parts[0] == "fb" and parts[2].isdigit():
        return parts[1], int(parts[2])
    return None


def publish_keyboard(story_id: int) -> InlineKeyboardMarkup:
    """Under a LinkedIn draft: the bot publishes it to his profile (spec: «Бот»)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Опубликовать в LinkedIn", callback_data=f"li:pub:{story_id}")]])


def published_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✓ Открыть пост", url=url)]])


def parse_publish(data: str | None) -> int | None:
    """story_id from «li:pub:<id>»."""
    parts = (data or "").split(":")
    return int(parts[2]) if len(parts) == 3 and parts[:2] == ["li", "pub"] and parts[2].isdigit() else None
