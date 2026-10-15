"""daily job: prepare today's digest and publish it to the channel in waves.

    python -m jobs.daily --prepare       # prefilter → enrich → score → select → mini-articles → overview; sends nothing
    python -m jobs.daily --wave N        # prepare what is missing, then send what is due by wave N
    python -m jobs.daily --preview       # today's pick in the channel format to the owner's DM only; no digest is saved
    python -m jobs.daily --preview --rewrite   # the same, articles written anew (after a prompt change)

Preparing is idempotent: the pick is made once a day, articles and the overview are written only where missing.
A wave sends every unsent entry whose wave has come, so a missed wave is caught up by the next one,
and a rerun sends nothing twice (each post's message id is stored right after sending).
"""

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import psycopg
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import LinkPreviewOptions
from openai import AsyncOpenAI

from adapters.rss import make_http_client
from bot.keyboards import feedback_keyboard
from config import Settings, get_settings
from core import article, digest, enrich, score
from core.llm import Usage, complete_json, load_prompt, make_client, render
from core.normalize import is_specific_url
from db import queries
from jobs.fetch import setup_logging

log = logging.getLogger("daily")
EARLY_HEADER = "🌱 <b>Ранние сигналы</b>\n<i>Уже на HN, arXiv и HF — в каналах ещё нет</i>"
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)  # the mini-article is the content; links are for checking


def today(s: Settings):
    return datetime.now(ZoneInfo(s.digest_tz)).date()


def make_bot(s: Settings) -> Bot:
    return Bot(s.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


@dataclass
class Ctx:
    conn: psycopg.Connection
    s: Settings
    llm: AsyncOpenAI
    http: httpx.AsyncClient
    usage: Usage
    stats: dict


# --- steps 5–8: prefilter, enrich, score, select -----------------------------------

async def rank(ctx: Ctx) -> list[digest.Candidate]:
    """Today's candidates after prefilter, enriched and scored (cached on stories: reruns call the LLM only for new ones)."""
    conn, s = ctx.conn, ctx.s
    profile = queries.active_profile(conn)
    if not profile:
        raise RuntimeError("no active taste profile: run jobs.bootstrap_taste and --activate it")
    profile_id, profile_text = profile
    cands = [
        digest.Candidate(
            r["story_id"], r["sim"] or 0.0, r["heat"], r["kinds"], manual=r["manual"], notes=r["notes"],
            category=r["category"], llm_score=r["llm_score"], avg_weight=float(r["avg_weight"] or 1.0),
            enriched=r["enriched"], owner_actions=frozenset(r["owner_actions"]),
        )
        for r in queries.ranking_candidates(conn, profile_id, s.candidate_window_h)
    ]
    queries.set_prefilter_sims(conn, [(c.prefilter_sim, c.story_id) for c in cands])
    conn.commit()
    pre = score.prefilter(cands, s.prefilter_n, s.prefilter_min_sim, s.heat_bypass)
    ctx.stats.update(candidates=len(cands), prefiltered=len(pre))

    need = [c.story_id for c in pre if not c.enriched]
    if need:
        inputs = [
            enrich.story_input(sid, title, text, [u for u in dict.fromkeys([canon, *urls]) if is_specific_url(u)][:5],
                               heat, kinds, s.enrich_text_chars)
            for sid, title, canon, heat, text, urls, kinds in queries.enrich_inputs(conn, need)
        ]
        records, failed = await enrich.run(ctx.llm, s, ctx.usage, inputs, s.enrich_batch)
        queries.set_enriched(conn, records)
        conn.commit()
        by_id = {r.story_id: r for r in records}
        for c in pre:
            if r := by_id.get(c.story_id):
                c.enriched, c.category = True, r.category
        pre = [c for c in pre if not (c.story_id in by_id and by_id[c.story_id].is_ad)]
        ctx.stats.update(enriched=len(records), ads=sum(r.is_ad for r in records), enrich_failed_batches=failed)

    need = [c.story_id for c in pre if c.enriched and c.llm_score is None]
    if need:
        scores, failed = await score.run(ctx.llm, s, ctx.usage, profile_text, queries.score_inputs(conn, need),
                                         s.score_batch)
        queries.set_scores(conn, scores)
        conn.commit()
        for c in pre:
            if c.story_id in scores:
                c.llm_score = scores[c.story_id][0]
        ctx.stats.update(scored=len(scores), score_failed_batches=failed)

    for c in pre:
        if c.llm_score is not None:
            c.final_score = score.final_score(c.llm_score, c.heat, c.avg_weight, set(c.owner_actions), s)
            c.is_early = score.is_early_signal(c.heat, c.llm_score, c.kinds)
    queries.set_final_scores(conn, [(c.final_score, c.is_early, c.story_id) for c in pre if c.final_score is not None])
    conn.commit()
    return pre


def pick(ctx: Ctx, pre: list[digest.Candidate]) -> tuple[list[digest.Candidate], list[digest.Candidate]]:
    s = ctx.s
    return digest.select(pre, s.digest_n, s.max_per_category, s.early_n, s.interim_heat_bonus)


async def build(ctx: Ctx, day) -> int:
    """Today's digest, created once; reruns reuse it."""
    found = queries.digest_for(ctx.conn, day)
    if found:
        return found[0]
    top, early = pick(ctx, await rank(ctx))
    entries = [(c.story_id, i, "top") for i, c in enumerate(top, 1)]
    entries += [(c.story_id, i, "early") for i, c in enumerate(early, 1)]
    digest_id = queries.create_digest(ctx.conn, day, entries)
    ctx.stats.update(built=True, top=len(top), early=len(early),
                     dismissed_stale=queries.dismiss_stale(ctx.conn, ctx.s.stale_after_h))
    ctx.conn.commit()
    return digest_id


def append_late_manual(ctx: Ctx, digest_id: int) -> int:
    """/add stories that got a story after the pick still join today's digest, out of turn."""
    profile = queries.active_profile(ctx.conn)
    rows = queries.ranking_candidates(ctx.conn, profile[0], ctx.s.candidate_window_h) if profile else []
    manual = [r["story_id"] for r in rows if r["manual"]]
    if not manual:
        return 0
    entries = queries.digest_entries(ctx.conn, digest_id)
    last = max((pos for _, pos, sec, _ in entries if sec == "top"), default=0)
    queries.add_digest_entries(ctx.conn, digest_id, [(sid, last + i, "top") for i, sid in enumerate(manual, 1)])
    ctx.conn.commit()
    return len(manual)


# --- step 9: mini-articles and the overview ----------------------------------------

async def write_article(ctx: Ctx, story_id: int, refresh_stale: bool, rewrite: bool = False) -> bool:
    """The story's mini-article, if missing (or older than its newest item, when refresh_stale; always, when
    rewrite). False on failure."""
    card = queries.story_card(ctx.conn, story_id)
    if card["article"] and not rewrite and not (refresh_stale and card["article_stale"]):
        return True
    main = next(iter(u for u in digest.story_links(card, is_specific_url) if is_specific_url(u)), None)
    primary = (main, await article.primary_text(ctx.http, main)) if main else None
    sources = article.materials(primary, card["items"], ctx.s.article_source_chars)
    prompt = load_prompt("article_ru")
    kinds = sorted({i["kind"] for i in card["items"]})
    user = render(prompt.user, n_sources=card["heat"], source_kinds=", ".join(kinds),
                  category=card["category"] or "unknown", tags=", ".join(card["tags"]) or "none", sources=sources)
    try:
        a = article.parse_article(await complete_json(ctx.llm, ctx.s, ctx.s.strong_model, prompt.system, user, ctx.usage))
    except Exception:
        log.exception("article for story %s failed", story_id)
        return False
    queries.set_article(ctx.conn, story_id, a, ctx.s.strong_model)
    ctx.conn.commit()
    return True


async def write_articles(ctx: Ctx, story_ids: list[int], refresh_stale: bool, rewrite: bool = False) -> None:
    failed = [sid for sid in story_ids if not await write_article(ctx, sid, refresh_stale, rewrite)]
    if failed:
        ctx.stats["article_failed"] = failed


async def make_overview(ctx: Ctx, day, top_ids: list[int]) -> dict | None:
    arts = [a for a in (queries.story_card(ctx.conn, sid)["article"] for sid in top_ids) if a]
    if not arts:
        return None
    prompt = load_prompt("overview_ru")
    user = render(prompt.user, date=day.isoformat(), stories=article.overview_stories(arts))
    try:
        return article.parse_overview(await complete_json(ctx.llm, ctx.s, ctx.s.strong_model, prompt.system, user, ctx.usage))
    except Exception:
        log.exception("overview failed")
        ctx.stats["overview_failed"] = True
        return None


async def prepare(ctx: Ctx, day) -> int:
    """Steps 5–9 for today, only what is missing. Articles are refreshed until the overview is written,
    then frozen so the overview's table of contents keeps matching the posts."""
    digest_id = await build(ctx, day)
    late = append_late_manual(ctx, digest_id)
    if late:
        ctx.stats["late_manual"] = late
    overview = queries.digest_overview(ctx.conn, digest_id)
    entries = queries.digest_entries(ctx.conn, digest_id)
    await write_articles(ctx, [sid for sid, _, _, msg in entries if msg is None], refresh_stale=overview is None)
    if overview is None:
        overview = await make_overview(ctx, day, [sid for sid, _, sec, _ in entries if sec == "top"])
        if overview:
            queries.set_digest_overview(ctx.conn, digest_id, overview)
            ctx.conn.commit()
    return digest_id


# --- step 10: sending ------------------------------------------------------------

def overview_text(conn, s: Settings, day, overview: dict | None, top_ids: list[int], has_early: bool,
                  waves: list[str]) -> str:
    titles = []
    for sid in top_ids:
        card = queries.story_card(conn, sid)
        titles.append((card["article"] or {}).get("title") or card["title"] or "")
    later = digest.later_lines(len(top_ids), has_early, waves, s.wave1_n)
    return article.render_overview(day, overview, titles, later)


def today_overview(conn, s: Settings, digest_id: int) -> str:
    """The opening post of a stored digest."""
    entries = queries.digest_entries(conn, digest_id)
    top_ids = [sid for sid, _, sec, _ in entries if sec == "top"]
    has_early = any(sec == "early" for _, _, sec, _ in entries)
    return overview_text(conn, s, today(s), queries.digest_overview(conn, digest_id), top_ids, has_early, s.digest_waves)


def entry_text(conn, story_id: int, position: int) -> str:
    card = queries.story_card(conn, story_id)
    meta = article.Meta(position, card["heat"], card["tags"], digest.story_links(card, is_specific_url), card["category"])
    if card["article"]:
        return article.render_article(card["article"], meta)
    return article.render_card(card["title"] or "", card["summary"], card["why_it_matters"], meta)


async def send_entry(bot: Bot, conn, chat_id: int, story_id: int, position: int) -> int:
    msg = await bot.send_message(
        chat_id, entry_text(conn, story_id, position),
        reply_markup=feedback_keyboard(story_id, queries.owner_actions(conn, story_id)),
        link_preview_options=NO_PREVIEW,
    )
    return msg.message_id


async def send_wave(ctx: Ctx, bot: Bot, day, wave: int) -> None:
    conn, s = ctx.conn, ctx.s
    digest_id = await prepare(ctx, day)
    _, header_msg = queries.digest_for(conn, day)
    entries = queries.digest_entries(conn, digest_id)
    due = [e for e in entries if e[3] is None and digest.wave_of(e[1], e[2], s.wave1_n) <= wave]
    ctx.stats.update(wave=wave, sent=0)
    if not due:
        return

    if header_msg is None:
        msg = await bot.send_message(s.digest_channel_id, today_overview(conn, s, digest_id), link_preview_options=NO_PREVIEW)
        queries.set_header_message(conn, digest_id, msg.message_id)
        conn.commit()
        await send_yesterday_line(conn, s, bot, day)

    early_started = any(e[2] == "early" and e[3] is not None for e in entries)
    for story_id, position, section, _ in due:
        if section == "early" and not early_started:
            await bot.send_message(s.digest_channel_id, EARLY_HEADER)
            early_started = True
        msg_id = await send_entry(bot, conn, s.digest_channel_id, story_id, position)
        queries.set_entry_message(conn, digest_id, story_id, msg_id)
        conn.commit()  # stored at once: a crash mid-wave never sends a post twice
        ctx.stats["sent"] += 1


async def send_yesterday_line(conn, s: Settings, bot: Bot, day) -> None:
    """«Вчера: 👍 3 · 👎 1 · ⭐ 1» goes to the owner's DM, never to the channel."""
    tz = ZoneInfo(s.digest_tz)
    start = datetime.combine(day - timedelta(days=1), datetime.min.time(), tz)
    counts = queries.feedback_counts(conn, start, start + timedelta(days=1))
    line = f"Вчера: 👍 {counts.get('up', 0)} · 👎 {counts.get('down', 0)} · ⭐ {counts.get('star', 0)}"
    await bot.send_message(s.owner_chat_id, line)


async def preview(ctx: Ctx, bot: Bot, rewrite: bool = False) -> None:
    """Today's pick in the channel format to the owner only. Enrich, score and articles are cached on the
    stories (the real run reuses them); no digest is created and nothing is marked digested."""
    s, day = ctx.s, today(ctx.s)
    top, early = pick(ctx, await rank(ctx))
    ids = [c.story_id for c in top + early]
    await write_articles(ctx, ids, refresh_stale=True, rewrite=rewrite)
    overview = await make_overview(ctx, day, [c.story_id for c in top])
    waves = s.digest_waves or Settings.model_fields["digest_waves"].default  # waves paused: show the usual times
    await bot.send_message(s.owner_chat_id, "Превью дайджеста — в канал не уходит.")
    text = overview_text(ctx.conn, s, day, overview, [c.story_id for c in top], bool(early), waves)
    await bot.send_message(s.owner_chat_id, text, link_preview_options=NO_PREVIEW)
    for i, c in enumerate(top, 1):
        await send_entry(bot, ctx.conn, s.owner_chat_id, c.story_id, i)
    if early:
        await bot.send_message(s.owner_chat_id, EARLY_HEADER)
        for i, c in enumerate(early, 1):
            await send_entry(bot, ctx.conn, s.owner_chat_id, c.story_id, i)
    ctx.stats.update(preview_top=len(top), preview_early=len(early))


async def run(mode: str, wave: int | None = None, rewrite: bool = False) -> dict:
    """mode: prepare | wave | preview; rewrite (preview only) writes the articles anew, for prompt changes."""
    s = get_settings()
    conn = queries.connect(s.database_url)
    run_id = queries.start_run(conn, {"prepare": "daily"}.get(mode, mode))
    ctx = Ctx(conn, s, make_client(s), make_http_client(s.http_timeout_s), Usage(), {})
    bot = make_bot(s) if mode != "prepare" else None
    try:
        if mode == "prepare":
            await prepare(ctx, today(s))
        elif mode == "wave":
            await send_wave(ctx, bot, today(s), wave)
        else:
            await preview(ctx, bot, rewrite)
    except Exception as e:
        conn.rollback()
        queries.finish_run(conn, run_id, _stats(ctx), error=repr(e))
        raise
    finally:
        await ctx.http.aclose()
        if bot:
            await bot.session.close()
    queries.finish_run(conn, run_id, _stats(ctx))
    log.info("%s done: %s", mode, _stats(ctx))
    conn.close()
    return ctx.stats


def _stats(ctx: Ctx) -> dict:
    return {**ctx.stats, "usd": round(ctx.usage.usd, 4), "llm": ctx.usage.as_stats()}


if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--wave", type=int, choices=[1, 2, 3])
    mode.add_argument("--preview", action="store_true")
    parser.add_argument("--rewrite", action="store_true", help="with --preview: write the articles anew")
    args = parser.parse_args()
    try:
        asyncio.run(run("prepare" if args.prepare else "preview" if args.preview else "wave", args.wave, args.rewrite))
    except Exception:
        log.exception("daily failed")
        sys.exit(1)
