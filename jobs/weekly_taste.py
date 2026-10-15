"""weekly_taste: a new taste profile version from the owner's signals (spec: «Профиль вкуса», weekly update).

    python -m jobs.weekly_taste             # build, activate, send what changed to the owner's DM
    python -m jobs.weekly_taste --preview   # build and send, keep the current version active

Positive: ⭐ and 👍 in the bot, /missed, notes channel posts; negative: 👎 in the bot.
Centroids from all of them (positive k = 5 / 8, negative k = 3 once there are 3 downvotes).
The text is not rewritten: prompt `taste_update` changes the active profile only where the signals
since it was built show it (no signals → same text, no call). Old versions stay; rollback is
`python -m jobs.bootstrap_taste --activate N`.
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

import numpy as np

from config import get_settings
from core import llm, taste
from db import queries
from jobs.daily import make_bot
from jobs.fetch import setup_logging

log = logging.getLogger("weekly_taste")


def _items(rows) -> list[str]:
    return [taste.format_item(taste.NoteItem(at, title, text, url, fwd, taste.SIGNAL_LABELS.get((action, origin))))
            for at, title, text, url, fwd, action, origin in rows]


async def run(preview: bool = False) -> dict:
    s = get_settings()
    conn = queries.connect(s.database_url)
    run_id = queries.start_run(conn, "weekly_taste")
    usage, stats = llm.Usage(), {"preview": preview}
    bot = make_bot(s)
    try:
        old = queries.active_profile_row(conn)
        pos_v, neg_v = queries.taste_vectors(conn, True), queries.taste_vectors(conn, False)
        if not pos_v:
            raise RuntimeError("no positive signals: run jobs.bootstrap_taste first")
        k_pos = s.taste_k_large if len(pos_v) >= s.taste_k_large_from else s.taste_k
        pos_c, pos_n = taste.kmeans(np.array(pos_v, dtype=np.float32), k_pos)
        k_neg = taste.neg_k(len(neg_v), s.taste_neg_k)
        neg = list(zip(*taste.kmeans(np.array(neg_v, dtype=np.float32), k_neg))) if k_neg else []

        if not old:
            raise RuntimeError("no active taste profile to update: run jobs.bootstrap_taste first")
        _, old_version, old_text, old_from, old_created = old
        since = (old_from or {}).get("until") or old_created.isoformat()  # signals the old version did not see
        now = datetime.now(timezone.utc)
        positive = _items(queries.taste_signals(conn, True, since, s.taste_recent_n))
        negative = _items(queries.taste_signals(conn, False, since, s.taste_recent_n))
        text = await taste.update_profile(llm.make_client(s), s, old_text, old_version, since[:10],
                                          positive, negative, usage)
        built_from = {"positive": len(pos_v), "negative": len(neg_v), "signals": queries.signal_counts(conn),
                      "k_pos": len(pos_c), "k_neg": len(neg), "updated_from": old_version, "since": since,
                      "new_positive": len(positive), "new_negative": len(negative),
                      "until": now.isoformat(), "model": s.taste_model or s.strong_model}
        profile_id, version = queries.create_taste_profile(conn, text, built_from, list(zip(pos_c, pos_n)), neg)
        if not preview:
            queries.activate_taste_profile(conn, version)
        conn.commit()

        diff = taste.profile_diff(old_text, text)
        missed = queries.missed_reasons(conn, 7)
        await bot.send_message(s.owner_chat_id, taste.diff_message(old_version, version, built_from, diff,
                                                                    missed, activated=not preview))
        stats.update(version=version, active=not preview, built_from=built_from, changed_lines=len(diff),
                     missed=missed, usd=round(usage.usd, 4), llm=usage.as_stats())
    except Exception as e:
        conn.rollback()
        queries.finish_run(conn, run_id, {**stats, "usd": round(usage.usd, 4)}, error=repr(e))
        raise
    finally:
        await bot.session.close()
    queries.finish_run(conn, run_id, stats)
    log.info("weekly_taste done: %s", stats)
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
        log.exception("weekly_taste failed")
        sys.exit(1)
