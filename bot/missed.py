"""/missed <url>: «I saw this somewhere else, not in Radar». Writes feedback(missed) and, in runs.stats,
where the pipeline lost the story — the main quality metric (spec: «Метрики»)."""

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from adapters import rss
from config import get_settings
from core import normalize
from core.missed import REASONS, Found, cut_reason
from db import queries

log = logging.getLogger(__name__)


def build_router(owner_id: int) -> Router:
    router = Router(name="missed")
    router.message.filter(F.chat.id == owner_id)

    @router.message(Command("missed"))
    async def missed(message: Message, command: CommandObject) -> None:
        url = (command.args or "").split()[0] if command.args else ""
        if not url:
            await message.answer("Формат: /missed &lt;url&gt; — ссылка на то, что Radar пропустил")
            return
        s = get_settings()
        async with rss.make_http_client(s.http_timeout_s) as http:
            url = (await normalize.canonical_urls([url], http, s.short_link_timeout_s))[0]
        with queries.connect(s.database_url) as conn:
            run_id = queries.start_run(conn, "missed")
            story_id = queries.find_story_by_url(conn, url)
            facts = queries.missed_facts(conn, story_id, s.candidate_window_h) if story_id else None
            reason = cut_reason(Found(**{k: facts[k] for k in Found.__dataclass_fields__}) if facts else None)
            if story_id is None:
                story_id = queries.create_missed_story(conn, url)
            queries.add_feedback(conn, story_id, "missed", "bot")
            stats = {"url": url, "story_id": story_id, "reason": reason}
            if facts:
                stats.update({k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                              for k, v in facts.items() if k in ("prefilter_sim", "llm_score", "final_score", "cutoff")})
            queries.finish_run(conn, run_id, stats)
        detail = ""
        if facts and reason == "prefilter" and facts["prefilter_sim"] is not None:
            detail = f" (похожесть {facts['prefilter_sim']:.2f}, порог {s.prefilter_min_sim:.2f})"
        elif facts and reason in ("score", "digest_limit") and facts["final_score"] is not None:
            detail = f" (оценка {facts['final_score']:.2f}" + (f", отобранные от {facts['cutoff']:.2f})" if facts["cutoff"] else ")")
        seen = f"\nВ базе с {facts['first_seen_at']:%d.%m}: {html.escape(facts['title'] or url)}" if facts else ""
        await message.answer(f"Записал missed. Причина: {REASONS[reason]}{detail}.{seen}\n"
                             "Учту в воскресной пересборке вкуса.")

    return router
