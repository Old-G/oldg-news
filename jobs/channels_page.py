"""The channel review page: every Telegram subscription with its volume, taste similarity and notes forwards,
with on/off switches. The data is the owner's own subscriptions, so the filled page never goes to git:

    docker compose run --rm -T worker python -m jobs.channels_page > .local/radar-channels.html

Then publish it (Claude: Artifact tool, same URL as in docs/channels.md, capabilities {"db": {}}).
The switches save to the artifact's `choices` collection; applying them is described in docs/channels.md.
"""

import json
import sys
from datetime import date
from pathlib import Path

from config import get_settings
from db import queries

TEMPLATE = Path(__file__).resolve().parent.parent / "tools" / "channels_page.html"
FAR_FROM_TASTE = 0.43  # average similarity below this and no forwards → suggested off


def why(r: dict) -> str:
    """Filter group: off · notes (he forwarded from it) · quiet (no posts in 7 days) · suggest_off · ok."""
    if not r["on"]:
        return "off"
    if r["fwd"]:
        return "notes"
    if r["n"] == 0:
        return "quiet"
    if r["sim"] is None or r["sim"] < FAR_FROM_TASTE or (r["close"] == 0 and r["n"] >= 5):
        return "suggest_off"
    return "ok"


def render(rows: list[dict], day: date) -> str:
    data = [{**r, "sim": float(r["sim"]) if r["sim"] is not None else None,
             "last": r["last"].isoformat() if r["last"] else None, "why": why(r)} for r in rows]
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]
    return TEMPLATE.read_text().replace("__DATA__", payload).replace("__DATE__", f"{day.day} {months[day.month - 1]} {day.year}")


if __name__ == "__main__":
    with queries.connect(get_settings().database_url) as conn:
        sys.stdout.write(render(queries.channel_review(conn), date.today()))
