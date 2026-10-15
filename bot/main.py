"""Bot process: long polling. Commands answer the owner only; digest buttons take anyone, split by origin."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot import add, commands, feedback
from config import get_settings

log = logging.getLogger("bot")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    if not s.bot_token or not s.owner_chat_id:
        raise RuntimeError("BOT_TOKEN and OWNER_CHAT_ID must be set in .env")
    bot = Bot(s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = Dispatcher()
    for module in (add, commands, feedback):
        dp.include_router(module.build_router(s.owner_chat_id))
    me = await bot.get_me()
    log.info("polling as @%s, owner %s", me.username, s.owner_chat_id)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
