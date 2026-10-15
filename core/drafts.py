"""Step 12 of the pipeline: drafts of his own posts for a ⭐ story — RU for Telegram (prompt `draft_ru`),
EN for X and LinkedIn (`draft_en`, off unless DRAFTS_EN). Strong model; the record is the English story, not the RU article,
so the EN posts are written from the record and never read as a translation."""

import re

_FENCE = re.compile(r"^```\w*\s*(.*?)\s*```$", re.S)


def story_record(card: dict, links: list[str]) -> dict:
    """The structured story (queries.story_card) the draft prompts take as {story_record_json}."""
    return {
        "title": card["title"],
        "summary": card["summary"],
        "why_it_matters": card["why_it_matters"],
        "category": card["category"],
        "tags": card["tags"],
        "n_sources": card["heat"],
        "links": [u for u in links if "t.me/" not in u][:3] or links[:1],
    }


def clean_ru(text: str) -> str:
    """The RU post as the model wrote it, minus a code fence around it; ValueError when empty."""
    t = text.strip()
    if m := _FENCE.match(t):
        t = m.group(1).strip()
    if not t:
        raise ValueError("draft_ru: empty answer")
    return t


def parse_en(answer) -> tuple[str, str]:
    """(x, linkedin) from the draft_en JSON; ValueError when either is missing."""
    if not isinstance(answer, dict):
        raise ValueError(f"draft_en: expected an object, got {type(answer).__name__}")
    x, li = answer.get("x"), answer.get("linkedin")
    if not (isinstance(x, str) and x.strip() and isinstance(li, str) and li.strip()):
        raise ValueError("draft_en: x and linkedin are required")
    return x.strip(), li.strip()
