"""hf_trending: what is trending on Hugging Face (models, datasets, spaces) → RawItem.

Source URL: https://huggingface.co/api/trending?type=model|dataset|space&limit=20 (the API caps limit at 20;
the spec's /api/models?sort=trending answers 400 since 2026). The news is a repo entering the list, so an
item is emitted once, when its id first shows up; the ids seen live in sources.last_external_id (JSON).
Models and datasets carry no description in the list: the text comes from their README.
"""

import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import httpx

from adapters import RawItem

log = logging.getLogger(__name__)
MAX_SEEN = 1000
README_CHARS = 2500
PREFIX = {"model": "", "dataset": "datasets/", "space": "spaces/"}


def repo_url(kind: str, repo_id: str) -> str:
    return f"https://huggingface.co/{PREFIX[kind]}{repo_id}"


def strip_front_matter(readme: str) -> str:
    """README without the YAML header, HTML comments and images; whitespace squeezed."""
    text = re.sub(r"\A---\n.*?\n---\n", "", readme, flags=re.S)
    text = re.sub(r"<!--.*?-->|!\[[^\]]*\]\([^)]*\)|<img[^>]*>", "", text, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def describe(kind: str, rd: dict) -> tuple[str, str]:
    """(title, text) from the trending entry's repoData."""
    rid = rd["id"]
    facts = [f"trending {kind} on Hugging Face", f"{rd.get('likes', 0)} likes"]
    if rd.get("downloads"):
        facts.append(f"{rd['downloads']} downloads")
    if rd.get("pipeline_tag"):
        facts.append(rd["pipeline_tag"])
    if rd.get("numParameters"):
        facts.append(f"{rd['numParameters'] / 1e9:.1f}B parameters")
    if kind == "space":
        title = rd.get("title") or rid
        desc = rd.get("shortDescription") or rd.get("ai_short_description") or ""
        return f"{title} ({rid})", "\n".join(p for p in (desc, ", ".join(facts)) if p)
    return rid, ", ".join(facts)


async def fetch(http: httpx.AsyncClient, source_url: str, cursor: str | None,
                source_key: str) -> tuple[list[RawItem], str, dict]:
    kind = parse_qs(urlsplit(source_url).query).get("type", ["model"])[0]
    r = await http.get(source_url)
    r.raise_for_status()
    entries = r.json()["recentlyTrending"]
    seen = json.loads(cursor) if cursor else []
    known, items = set(seen), []
    stats = {"listed": len(entries), "new": 0}
    for e in entries:
        rd = e["repoData"]
        key = f"{kind}:{rd['id']}"
        if key in known or rd.get("private"):
            continue
        seen.insert(0, key)
        title, text = describe(kind, rd)
        if kind != "space":
            try:
                rm = await http.get(f"{repo_url(kind, rd['id'])}/raw/main/README.md")
                if rm.status_code == 200:
                    text += "\n\n" + strip_front_matter(rm.text)[:README_CHARS]
            except httpx.HTTPError as err:
                log.warning("README of %s failed: %r", rd["id"], err)
        url = repo_url(kind, rd["id"])
        items.append(RawItem(source_key, key, url, title, text, datetime.now(timezone.utc),
                             {"urls": [url], "via": "hf_trending", "likes": rd.get("likes")}))
        stats["new"] += 1
    return items, json.dumps(seen[:MAX_SEEN]), stats
