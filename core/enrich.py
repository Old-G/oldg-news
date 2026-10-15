"""Step 6 of the pipeline: a story record from its raw items (prompt `enrich`, cheap model, batches of 10)."""

import json
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from config import Settings
from core.llm import Usage, complete_json, load_prompt

log = logging.getLogger(__name__)
CATEGORIES = {"release", "paper", "tool", "infra", "agent", "business", "other"}
TITLE_MAX = 90


@dataclass
class Enriched:
    story_id: int
    title_clean: str
    summary: str
    why_it_matters: str
    category: str
    tags: list[str]
    is_ad: bool
    confidence: float | None


def story_input(story_id: int, title: str | None, text: str | None, urls: list[str], n_sources: int,
                kinds: list[str], chars: int) -> dict:
    """One element of the prompt's input array."""
    return {"story_id": story_id, "title": title or "", "text": (text or "")[:chars], "urls": urls,
            "n_sources": n_sources, "source_kinds": kinds}


def parse(answer, story_ids: set[int]) -> list[Enriched]:
    """Records for the stories that were asked about; malformed ones are dropped (retried next run)."""
    if isinstance(answer, dict):  # a single record not wrapped in an array
        answer = [answer]
    if not isinstance(answer, list):
        raise ValueError(f"enrich: expected a JSON array, got {type(answer).__name__}")
    out = []
    for r in answer:
        if not isinstance(r, dict) or r.get("story_id") not in story_ids:
            continue
        title, summary = _text(r.get("title_clean")), _text(r.get("summary"))
        if not title or not summary:
            continue
        category = _text(r.get("category")).lower()
        tags = r.get("tags") if isinstance(r.get("tags"), list) else []
        conf = r.get("confidence")
        out.append(Enriched(
            story_id=r["story_id"],
            title_clean=title if len(title) <= TITLE_MAX else title[:TITLE_MAX - 1].rstrip() + "…",
            summary=summary,
            why_it_matters=_text(r.get("why_it_matters")),
            category=category if category in CATEGORIES else "other",
            tags=list(dict.fromkeys(_text(t).lower() for t in tags if _text(t)))[:5],
            is_ad=r.get("is_ad") is True,
            confidence=float(conf) if isinstance(conf, (int, float)) else None,
        ))
    return out


def _text(v) -> str:
    return " ".join(v.split()) if isinstance(v, str) else ""


async def run(client: AsyncOpenAI, s: Settings, usage: Usage, inputs: list[dict], batch: int = 10) -> tuple[list[Enriched], int]:
    """(records, failed batches). A batch that fails twice is skipped: its stories stay unenriched."""
    prompt = load_prompt("enrich")
    done, failed = [], 0
    for i in range(0, len(inputs), batch):
        chunk = inputs[i:i + batch]
        try:
            answer = await complete_json(client, s, s.cheap_model, prompt.system,
                                         json.dumps(chunk, ensure_ascii=False), usage)
            done += parse(answer, {x["story_id"] for x in chunk})
        except Exception:
            log.exception("enrich batch %s failed", [x["story_id"] for x in chunk])
            failed += 1
    return done, failed
