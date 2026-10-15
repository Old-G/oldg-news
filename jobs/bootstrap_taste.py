"""bootstrap_taste: the notes channel becomes the first taste profile (spec: «Профиль вкуса», bootstrap).

    python -m jobs.bootstrap_taste                          # export, embed, centroids, draft profile (inactive)
    python -m jobs.bootstrap_taste --activate 1 [--text-file edited.txt]   # after reading it by eye

Rerunnable: known posts are skipped, every run writes a new inactive profile version.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import numpy as np
from telethon import utils

from adapters import rss, telegram
from config import get_settings
from core import embed, llm, taste
from db import queries
from jobs.fetch import canonicalize, normalize_pending

log = logging.getLogger("bootstrap_taste")


async def export_notes(conn, http) -> tuple[int, dict]:
    """Every post of the notes channel → items (label source)."""
    s = get_settings()
    client = telegram.make_client(s.tg_api_id, s.tg_api_hash, s.tg_session)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("TG_SESSION is missing or revoked: run `python -m jobs.tg_login`")
        entity = await client.get_entity(s.notes_channel_id)
        src = queries.upsert_source(conn, "telegram", str(utils.get_peer_id(entity)), entity.title, is_label_source=True)
        messages = [m async for m in client.iter_messages(entity)]
    finally:
        await client.disconnect()
    items, skipped = telegram.messages_to_items(messages, s.notes_channel_id, getattr(entity, "username", None))
    await canonicalize(items, http)
    new = queries.insert_items(conn, src.id, items)
    queries.update_source_cursor(conn, src.id, str(max(m.id for m in messages)) if messages else None)
    conn.commit()
    return src.id, {"messages": len(messages), "items": len(items), "items_new": new, "skipped_no_text": skipped}


async def bootstrap() -> None:
    s = get_settings()
    conn = queries.connect(s.database_url)
    run_id = queries.start_run(conn, "bootstrap_taste")
    stats: dict = {}
    usage = llm.Usage()
    try:
        async with rss.make_http_client(s.http_timeout_s) as http:
            source_id, stats["export"] = await export_notes(conn, http)
        stats["normalize"] = normalize_pending(conn)
        openai = llm.make_client(s)
        stats["embed"] = await embed.embed_pending(conn, openai, s)
        stats["note_stories"] = queries.bootstrap_note_stories(conn, source_id)
        conn.commit()

        rows = queries.note_items(conn, source_id)
        vectors = np.array([r[5].to_numpy() for r in rows], dtype=np.float32)
        k = s.taste_k_large if len(rows) >= s.taste_k_large_from else s.taste_k
        centroids, sizes = taste.kmeans(vectors, k)
        stats["centroids"] = {"k": len(centroids), "sizes": sizes.tolist()}

        recent = [taste.NoteItem(*r[:5]) for r in rows[: s.taste_recent_n]]
        text = await taste.write_profile(openai, s, [taste.format_item(it) for it in recent], [], usage)
        built_from = {"stars": len(rows), "downs": 0, "profile_from": len(recent),
                      "until": rows[0][0].date().isoformat() if rows and rows[0][0] else None,
                      "model": s.taste_model or s.strong_model}
        profile_id, version = queries.create_taste_profile(conn, text, built_from, list(zip(centroids, sizes)))
        conn.commit()
        stats["profile"] = {"id": profile_id, "version": version, "chars": len(text), "words": len(text.split())}
        stats["llm"] = usage.as_stats()
        stats["usd"] = round(stats["embed"]["usd"] + usage.usd, 4)
    except Exception as e:
        conn.rollback()
        stats["llm"] = usage.as_stats()
        queries.finish_run(conn, run_id, stats, error=repr(e))
        raise
    queries.finish_run(conn, run_id, stats)
    log.info("run %s done: %s", run_id, stats)
    print(f"\n=== taste profile v{version} (inactive, read and edit it, then --activate {version}) ===\n")
    print(text)
    conn.close()


def activate(version: int, text_file: str | None) -> None:
    s = get_settings()
    text = Path(text_file).read_text().strip() if text_file else None
    with queries.connect(s.database_url) as conn:
        queries.activate_taste_profile(conn, version, text)
        active = conn.execute("select version, length(profile_text) from taste_profiles where is_active").fetchall()
    print(f"active profile: {active}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--activate", type=int, metavar="VERSION")
    parser.add_argument("--text-file")
    args = parser.parse_args()
    if args.activate:
        activate(args.activate, args.text_file)
    else:
        try:
            asyncio.run(bootstrap())
        except Exception:
            log.exception("bootstrap failed")
            sys.exit(1)
