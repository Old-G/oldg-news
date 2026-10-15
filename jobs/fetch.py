"""fetch job, pipeline steps 1–4: sources → items → normalize → embed → stories."""

import asyncio
import logging
import random
import sys
from datetime import datetime, timedelta, timezone

import httpx
from openai import AsyncOpenAI
from telethon import utils

from adapters import RawItem, rss, telegram
from config import get_settings
from core import cluster, embed, normalize
from db import queries

log = logging.getLogger("fetch")


async def canonicalize(items: list[RawItem], http: httpx.AsyncClient) -> None:
    timeout = get_settings().short_link_timeout_s
    for it in items:
        urls = it.raw.get("urls") or ([it.url] if it.url else [])
        it.raw["urls"] = await normalize.canonical_urls(urls, http, timeout)
        if it.url:
            it.url = (await normalize.canonical_urls([it.url], http, timeout))[0]


async def fetch_telegram(conn, stats: dict, http: httpx.AsyncClient) -> None:
    s = get_settings()
    st = stats["telegram"]
    client = telegram.make_client(s.tg_api_id, s.tg_api_hash, s.tg_session)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("TG_SESSION is missing or revoked: run `python -m jobs.tg_login`")
        exclude = {s.digest_channel_id} - {None}
        dialogs = await telegram.with_floodwait(
            lambda: telegram.list_channels(client, exclude), s.tg_floodwait_retries, st
        )
        st["channels"] = len(dialogs)
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
                    st,
                )
                items, skipped = telegram.messages_to_items(
                    messages, peer_id, getattr(d.entity, "username", None)
                )
                await canonicalize(items, http)
                new = queries.insert_items(conn, src.id, items)
                queries.update_source_cursor(conn, src.id, str(newest_id) if newest_id else None)
                conn.commit()
            except Exception as e:
                # one broken channel must not stop the others; it is recorded in runs.stats
                conn.rollback()
                log.exception("channel %s (%s) failed", d.title, peer_id)
                st.setdefault("errors", []).append(f"{peer_id}: {e!r}")
                continue
            st["messages_seen"] += len(messages)
            st["items_seen"] += len(items)
            st["items_new"] += new
            st["skipped_no_text"] += skipped
            await asyncio.sleep(random.uniform(s.tg_pause_min_s, s.tg_pause_max_s))
    finally:
        await client.disconnect()


async def fetch_feeds(conn, stats: dict, http: httpx.AsyncClient) -> None:
    s = get_settings()
    st = stats["feeds"]
    since = datetime.now(timezone.utc) - timedelta(hours=s.feed_max_age_h)
    for src in queries.feed_sources(conn):
        st["sources"] += 1
        try:
            items = await rss.fetch(http, src.kind, src.external_id, src.external_id)
            fresh = [it for it in items if it.published_at and it.published_at >= since]
            st["undated"] += sum(1 for it in items if not it.published_at)
            st["too_old"] += sum(1 for it in items if it.published_at and it.published_at < since)
            await canonicalize(fresh, http)
            if src.kind == "arxiv":
                # a cross-listed paper shows up in several categories: keep it once
                seen = queries.existing_urls(conn, "arxiv", [it.url for it in fresh if it.url])
                st["arxiv_already_stored"] += sum(1 for it in fresh if it.url in seen)
                fresh = [it for it in fresh if it.url not in seen]
            new = queries.insert_items(conn, src.id, fresh)
            newest = max(fresh, key=lambda it: it.published_at, default=None)
            queries.update_source_cursor(conn, src.id, newest.external_id if newest else None)
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.warning("feed %s (%s) failed: %r", src.title, src.external_id, e)
            st.setdefault("errors", []).append(f"{src.title}: {e!r}"[:300])
            continue
        st["items_seen"] += len(fresh)
        st["items_new"] += new
        st["by_kind"][src.kind] = st["by_kind"].get(src.kind, 0) + new


def normalize_pending(conn, batch: int = 500) -> dict:
    """Clean text, pick the main link, language, ads. Marks an item done by setting lang; safe to rerun."""
    stats = {"normalized": 0, "ads": 0}
    while rows := queries.items_to_normalize(conn, batch):
        updates = []
        for r in rows:
            text = normalize.clean_text(r.text)
            url = normalize.primary_url(r.urls) if r.kind == "telegram" else r.url
            own = r.link.split("/")[3] if r.link and r.link.count("/") >= 4 else None
            # a post saved to the notes channel is a taste label, whatever it looks like
            ad = not r.is_label_source and normalize.is_ad(r.text, r.urls, r.kind, own)
            updates.append((text, url, normalize.content_hash(url, text), normalize.detect_lang(text or r.title), ad, r.id))
            stats["ads"] += ad
        queries.set_normalized(conn, updates)
        conn.commit()
        stats["normalized"] += len(rows)
    return stats


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = get_settings()
    conn = queries.connect(s.database_url)
    run_id = queries.start_run(conn, "fetch")
    stats = {
        "telegram": {"channels": 0, "messages_seen": 0, "items_seen": 0, "items_new": 0, "skipped_no_text": 0},
        "feeds": {"sources": 0, "items_seen": 0, "items_new": 0, "too_old": 0, "undated": 0,
                  "arxiv_already_stored": 0, "by_kind": {}},
    }
    errors = []
    async with rss.make_http_client(s.http_timeout_s) as http:
        for name, part in (("telegram", fetch_telegram), ("feeds", fetch_feeds)):
            try:
                await part(conn, stats, http)
            except Exception as e:
                # a dead Telegram session must not stop the feeds, and vice versa
                conn.rollback()
                log.exception("%s failed", name)
                errors.append(f"{name}: {e!r}")
    stats["items_new"] = stats["telegram"]["items_new"] + stats["feeds"]["items_new"]

    async def run_stage(name: str) -> dict:
        if name == "normalize":
            return normalize_pending(conn)
        if name == "embed":
            return await embed.embed_pending(conn, AsyncOpenAI(api_key=s.openai_api_key), s)
        return cluster.cluster_pending(conn, s)

    for name in ("normalize", "embed", "cluster"):
        try:
            stats[name] = await run_stage(name)
        except Exception as e:
            # cluster still runs on whatever got embedded; the next run picks up the rest
            conn.rollback()
            log.exception("%s failed", name)
            errors.append(f"{name}: {e!r}")
    stats["usd"] = stats.get("embed", {}).get("usd", 0.0)
    queries.finish_run(conn, run_id, stats, error="; ".join(errors) or None)
    log.info("run %s done: %s", run_id, stats)
    conn.close()
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
