"""daily job: pick the day's stories (wave 1) and publish them to the channel in waves.

    python -m jobs.daily --wave 1        # build today's digest if missing, send what is due by wave 1
    python -m jobs.daily --wave 2        # send what is due by wave 2 (catches up anything unsent)
    python -m jobs.daily --preview       # today's pick to the owner's DM only; nothing is saved

A wave sends every unsent entry whose wave has come, so a missed wave is caught up by the next one,
and a rerun sends nothing twice (each post's message id is stored right after sending).
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import LinkPreviewOptions

from bot.keyboards import feedback_keyboard
from config import Settings, get_settings
from core import digest
from core.normalize import is_specific_url
from db import queries
from jobs.fetch import setup_logging

log = logging.getLogger("daily")
EARLY_HEADER = "🌱 Ранние сигналы — есть на HN/arXiv/HF, в каналах ещё нет"


def today(s: Settings):
    return datetime.now(ZoneInfo(s.digest_tz)).date()


def make_bot(s: Settings) -> Bot:
    return Bot(s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def pick(conn, s: Settings) -> list[digest.Candidate]:
    profile_id = queries.active_profile_id(conn)
    if not profile_id:
        raise RuntimeError("no active taste profile: run jobs.bootstrap_taste and --activate it")
    cands = [
        digest.Candidate(sid, sim or 0.0, heat, kinds, manual)
        for sid, sim, heat, kinds, manual in queries.digest_candidates(conn, profile_id, s.candidate_window_h)
    ]
    queries.set_prefilter_sims(conn, [(c.prefilter_sim, c.story_id) for c in cands])
    return digest.select(cands, s.digest_n, s.prefilter_min_sim, s.interim_heat_bonus)


def build(conn, s: Settings, day) -> tuple[int, dict]:
    """Today's digest, created once; reruns reuse it."""
    found = queries.digest_for(conn, day)
    if found:
        return found[0], {"built": False}
    picked = pick(conn, s)
    digest_id = queries.create_digest(conn, day, [(c.story_id, i, "top") for i, c in enumerate(picked, 1)])
    dismissed = queries.dismiss_stale(conn, s.stale_after_h)
    conn.commit()
    return digest_id, {"built": True, "picked": len(picked), "dismissed_stale": dismissed}


def append_late_manual(conn, s: Settings, digest_id: int) -> int:
    """/add stories that got a story after the pick still join today's digest, out of turn."""
    profile_id = queries.active_profile_id(conn)
    manual = [r[0] for r in queries.digest_candidates(conn, profile_id, s.candidate_window_h) if r[4]]
    if not manual:
        return 0
    last = max((pos for _, pos, sec, _ in queries.digest_entries(conn, digest_id) if sec == "top"), default=0)
    queries.add_digest_entries(conn, digest_id, [(sid, last + i, "top") for i, sid in enumerate(manual, 1)])
    conn.commit()
    return len(manual)


async def send_story(bot: Bot, conn, chat_id: int, story_id: int, position: int) -> int:
    view = digest.view_from_card(queries.story_card(conn, story_id), position, is_specific_url)
    preview = LinkPreviewOptions(url=view.urls[0], prefer_small_media=True) if view.urls else LinkPreviewOptions(is_disabled=True)
    msg = await bot.send_message(
        chat_id, digest.render_story(view),
        reply_markup=feedback_keyboard(story_id, queries.owner_actions(conn, story_id)),
        link_preview_options=preview,
    )
    return msg.message_id


async def send_wave(conn, s: Settings, bot: Bot, day, wave: int) -> dict:
    found = queries.digest_for(conn, day)
    if not found:
        return {"wave": wave, "sent": 0, "note": "no digest for today"}
    digest_id, header_msg = found
    late = append_late_manual(conn, s, digest_id)
    entries = queries.digest_entries(conn, digest_id)
    due = [e for e in entries if e[3] is None and digest.wave_of(e[1], e[2], s.wave1_n) <= wave]
    stats = {"wave": wave, "sent": 0, "late_manual": late}
    if not due:
        return stats

    if header_msg is None:
        n_top = sum(1 for e in entries if e[2] == "top")
        n_early = sum(1 for e in entries if e[2] == "early")
        msg = await bot.send_message(s.digest_channel_id, digest.header(day, n_top, n_early))
        queries.set_header_message(conn, digest_id, msg.message_id)
        conn.commit()
        await send_yesterday_line(conn, s, bot, day)

    early_started = any(e[2] == "early" and e[3] is not None for e in entries)
    for story_id, position, section, _ in due:
        if section == "early" and not early_started:
            await bot.send_message(s.digest_channel_id, EARLY_HEADER)
            early_started = True
        msg_id = await send_story(bot, conn, s.digest_channel_id, story_id, position)
        queries.set_entry_message(conn, digest_id, story_id, msg_id)
        conn.commit()  # stored at once: a crash mid-wave never sends a post twice
        stats["sent"] += 1
    return stats


async def send_yesterday_line(conn, s: Settings, bot: Bot, day) -> None:
    """«Вчера: 👍 3 · 👎 1 · ⭐ 1» goes to the owner's DM, never to the channel."""
    tz = ZoneInfo(s.digest_tz)
    start = datetime.combine(day - timedelta(days=1), datetime.min.time(), tz)
    counts = queries.feedback_counts(conn, start, start + timedelta(days=1))
    line = f"Вчера: 👍 {counts.get('up', 0)} · 👎 {counts.get('down', 0)} · ⭐ {counts.get('star', 0)}"
    await bot.send_message(s.owner_chat_id, line)


async def preview(conn, s: Settings, bot: Bot) -> dict:
    """Today's pick to the owner only; nothing is written except prefilter_sim."""
    picked = pick(conn, s)
    conn.commit()
    await bot.send_message(s.owner_chat_id, "Превью дайджеста — в канал не уходит:\n" + digest.header(today(s), len(picked), 0))
    for i, c in enumerate(picked, 1):
        await send_story(bot, conn, s.owner_chat_id, c.story_id, i)
    return {"preview": len(picked)}


async def run(wave: int | None = None, preview_only: bool = False) -> dict:
    s = get_settings()
    conn = queries.connect(s.database_url)
    job = "preview" if preview_only else ("daily" if wave == 1 else "wave")
    run_id = queries.start_run(conn, job)
    bot = make_bot(s)
    stats: dict = {}
    try:
        if preview_only:
            stats = await preview(conn, s, bot)
        else:
            day = today(s)
            if wave == 1:
                _, stats["build"] = build(conn, s, day)
            stats.update(await send_wave(conn, s, bot, day, wave))
    except Exception as e:
        conn.rollback()
        queries.finish_run(conn, run_id, stats, error=repr(e))
        raise
    finally:
        await bot.session.close()
    queries.finish_run(conn, run_id, stats)
    log.info("%s done: %s", job, stats)
    conn.close()
    return stats


if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--wave", type=int, choices=[1, 2, 3])
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    if not args.preview and not args.wave:
        parser.error("--wave N or --preview")
    try:
        asyncio.run(run(args.wave, args.preview))
    except Exception:
        log.exception("daily failed")
        sys.exit(1)
