"""fetch job: every enabled source → items. Steps 1–4 of the pipeline; step 1 covers Telegram only."""

import asyncio
import logging
import random

from telethon import utils

from adapters import telegram
from config import get_settings
from db import queries

log = logging.getLogger("fetch")


async def fetch_telegram(conn, stats: dict) -> None:
    s = get_settings()
    client = telegram.make_client(s.tg_api_id, s.tg_api_hash, s.tg_session)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("TG_SESSION is missing or revoked: run `python -m jobs.tg_login`")
        exclude = {s.digest_channel_id} - {None}
        dialogs = await telegram.with_floodwait(
            lambda: telegram.list_channels(client, exclude), s.tg_floodwait_retries, stats
        )
        stats["channels"] = len(dialogs)
        for d in dialogs:
            peer_id = utils.get_peer_id(d.entity)
            src = queries.upsert_source(
                conn, "telegram", str(peer_id), d.title, is_label_source=peer_id == s.notes_channel_id
            )
            conn.commit()
            if not src.enabled:
                continue
            try:
                messages, newest_id = await telegram.with_floodwait(
                    lambda: telegram.fetch_messages(
                        client, d.entity, int(src.last_external_id or 0) or None, s.tg_backfill_hours
                    ),
                    s.tg_floodwait_retries,
                    stats,
                )
                items, skipped = telegram.messages_to_items(
                    messages, peer_id, getattr(d.entity, "username", None)
                )
                new = queries.insert_items(conn, src.id, items)
                queries.update_source_cursor(conn, src.id, str(newest_id) if newest_id else None)
                conn.commit()
            except Exception as e:
                # one broken channel must not stop the others; it is recorded in runs.stats
                conn.rollback()
                log.exception("channel %s (%s) failed", d.title, peer_id)
                stats.setdefault("channel_errors", []).append(f"{peer_id}: {e!r}")
                continue
            stats["messages_seen"] += len(messages)
            stats["items_seen"] += len(items)
            stats["items_new"] += new
            stats["skipped_no_text"] += skipped
            await asyncio.sleep(random.uniform(s.tg_pause_min_s, s.tg_pause_max_s))
    finally:
        await client.disconnect()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn = queries.connect(get_settings().database_url)
    run_id = queries.start_run(conn, "fetch")
    stats = {"channels": 0, "messages_seen": 0, "items_seen": 0, "items_new": 0, "skipped_no_text": 0}
    try:
        await fetch_telegram(conn, stats)
    except Exception as e:
        conn.rollback()
        queries.finish_run(conn, run_id, stats, error=repr(e))
        raise
    queries.finish_run(conn, run_id, stats)
    log.info("run %s done: %s", run_id, stats)
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
