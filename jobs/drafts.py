"""drafts job: for every ⭐ the owner pressed, a draft of his own RU Telegram post to his DM (spec step 12);
with DRAFTS_EN also EN drafts for X and LinkedIn.

    python -m jobs.drafts      # handle the pending ⭐ once

The worker runs it every DRAFTS_POLL_S seconds; a run without pending ⭐ writes nothing, not even a runs row.
Messages go out first and the drafts are stored after, so a crash in between repeats the drafts rather
than losing them. A story whose drafts fail three times in a row is left until the worker restarts.
"""

import asyncio
import json
import logging
import sys

from aiogram.types import LinkPreviewOptions

from adapters.rss import make_http_client
from config import Settings, get_settings
from core import article, digest, drafts
from core.llm import Usage, complete, complete_json, load_prompt, make_client, render
from core.normalize import is_specific_url
from db import queries
from jobs.daily import make_bot
from jobs.fetch import setup_logging

log = logging.getLogger("drafts")
MAX_ATTEMPTS = 3
_failures: dict[int, int] = {}  # story_id → failed attempts in this process


async def write(s: Settings, conn, llm, http, usage: Usage, story_id: int) -> list[tuple[str, str, str]]:
    """[(lang, channel, text)]: the RU Telegram post; with DRAFTS_EN also the X and the LinkedIn post."""
    card = queries.story_card(conn, story_id)
    links = digest.story_links(card, is_specific_url)
    main = next((u for u in links if is_specific_url(u)), None)
    primary = (main, await article.primary_text(http, main)) if main else None
    values = {"story_record_json": json.dumps(drafts.story_record(card, links), ensure_ascii=False),
              "best_item_text": article.materials(primary, card["items"], s.draft_source_chars)}
    ru_p = load_prompt("draft_ru")
    ru = drafts.clean_ru(await complete(llm, s, s.strong_model, ru_p.system, render(ru_p.user, **values), usage))
    if not s.drafts_en:
        return [("ru", "tg", ru)]
    en_p = load_prompt("draft_en")
    x, linkedin = drafts.parse_en(
        await complete_json(llm, s, s.strong_model, en_p.system, render(en_p.user, **values), usage))
    return [("ru", "tg", ru), ("en", "x", x), ("en", "linkedin", linkedin)]


async def run() -> dict | None:
    s = get_settings()
    conn = queries.connect(s.database_url)
    pending = [sid for sid in queries.stars_without_drafts(conn, s.drafts_window_h)
               if _failures.get(sid, 0) < MAX_ATTEMPTS]
    if not pending:
        conn.close()
        return None
    run_id = queries.start_run(conn, "drafts")
    usage, stats = Usage(), {"pending": len(pending), "sent": [], "failed": []}
    llm, http, bot = make_client(s), make_http_client(s.http_timeout_s), make_bot(s)
    try:
        for sid in pending:
            try:
                texts = await write(s, conn, llm, http, usage, sid)
            except Exception:
                log.exception("drafts for story %s failed", sid)
                _failures[sid] = _failures.get(sid, 0) + 1
                stats["failed"].append(sid)
                continue
            for _, _, text in texts:  # plain text, no markup: copied as is
                await bot.send_message(s.owner_chat_id, text, parse_mode=None,
                                       link_preview_options=LinkPreviewOptions(is_disabled=True))
            queries.add_drafts(conn, sid, texts, s.strong_model)
            conn.commit()
            stats["sent"].append(sid)
    finally:
        await http.aclose()
        await bot.session.close()
    stats.update(usd=round(usage.usd, 4), llm=usage.as_stats())
    queries.finish_run(conn, run_id, stats, error=f"failed: {stats['failed']}" if stats["failed"] else None)
    log.info("drafts done: %s", stats)
    conn.close()
    return stats


if __name__ == "__main__":
    setup_logging()
    try:
        asyncio.run(run())
    except Exception:
        log.exception("drafts failed")
        sys.exit(1)
