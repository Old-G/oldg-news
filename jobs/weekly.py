"""weekly job: «Неделя: куда движемся» — the week's published stories → one post in the channel (spec step 13).

    python -m jobs.weekly             # write this week's post if missing and publish it, once
    python -m jobs.weekly --preview   # write it and send to the owner's DM only

The week is Monday–Sunday of the digest timezone; only stories that went out to the channel count.
"""

import argparse
import asyncio
import logging
import sys
from datetime import timedelta

from openai import AsyncOpenAI

from config import get_settings
from core import article
from core.llm import Usage, complete_json, load_prompt, render
from db import queries
from jobs.daily import NO_PREVIEW, make_bot, today
from jobs.fetch import setup_logging

log = logging.getLogger("weekly")


async def run(preview_only: bool = False) -> dict:
    s = get_settings()
    conn = queries.connect(s.database_url)
    run_id = queries.start_run(conn, "weekly")
    usage, stats = Usage(), {}
    end = today(s)
    start = end - timedelta(days=end.weekday())
    bot = make_bot(s)
    try:
        found = queries.weekly_post(conn, start)
        if found and found[1] is not None and not preview_only:
            stats["note"] = "already published"
        else:
            body = found[0] if found else None
            if body is None:
                rows = queries.week_articles(conn, start, end)
                stats["stories"] = len(rows)
                if rows:
                    prompt = load_prompt("weekly_ru")
                    user = render(prompt.user, week=f"{start.isoformat()} – {end.isoformat()}",
                                  stories=article.weekly_stories([(a, set(acts)) for a, acts in rows]))
                    client = AsyncOpenAI(api_key=s.openai_api_key)
                    body = article.parse_weekly(
                        await complete_json(client, s, s.strong_model, prompt.system, user, usage))
                    queries.save_weekly_post(conn, start, body, s.strong_model)
                    conn.commit()
            if body is None:
                stats["note"] = "no published stories this week"
            else:
                text = article.render_weekly(start, end, body)
                if preview_only:
                    await bot.send_message(s.owner_chat_id, "Превью недельного поста — в канал не уходит.")
                    await bot.send_message(s.owner_chat_id, text, link_preview_options=NO_PREVIEW)
                    stats["preview"] = True
                else:
                    msg = await bot.send_message(s.digest_channel_id, text, link_preview_options=NO_PREVIEW)
                    queries.set_weekly_message(conn, start, msg.message_id)
                    conn.commit()
                    stats["published"] = msg.message_id
    except Exception as e:
        conn.rollback()
        queries.finish_run(conn, run_id, {**stats, "usd": round(usage.usd, 4)}, error=repr(e))
        raise
    finally:
        await bot.session.close()
    stats.update(usd=round(usage.usd, 4), llm=usage.as_stats())
    queries.finish_run(conn, run_id, stats)
    log.info("weekly done: %s", stats)
    conn.close()
    return stats


if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.preview))
    except Exception:
        log.exception("weekly failed")
        sys.exit(1)
