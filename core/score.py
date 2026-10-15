"""Steps 5 and 7 of the pipeline: prefilter by taste similarity, LLM relevance score (prompt `score`,
cheap model, batches of 15) and the final formula."""

import json
import logging

from openai import AsyncOpenAI

from config import Settings
from core.llm import Usage, complete_json, load_prompt, render

log = logging.getLogger(__name__)
PAPER_KINDS = {"arxiv", "hf_papers"}
# sources where a story shows up before the Telegram channels pick it up; 'github' = an rss feed on github.com
EARLY_KINDS = {"hn", "hf_papers", "arxiv", "github"}
HEAT_CAP = 8


def prefilter(cands: list, n: int, min_sim: float, heat_bypass: int) -> list:
    """Spec step 5: top n by prefilter_sim, plus every story with heat ≥ heat_bypass, plus manual and notes.
    A paper nobody else picked up and below min_sim is cut first."""
    keep = [c for c in cands if not (set(c.kinds) <= PAPER_KINDS and c.heat == 1 and c.prefilter_sim < min_sim)]
    top = {c.story_id for c in sorted(keep, key=lambda c: c.prefilter_sim, reverse=True)[:n]}
    return [c for c in keep if c.story_id in top or c.heat >= heat_bypass or c.manual or c.notes]


def src_weight_norm(avg_weight: float) -> float:
    """Source weights live in 0.5–1.5; mapped to 0–1."""
    return min(max(avg_weight - 0.5, 0.0), 1.0)


def final_score(llm_score: float, heat: int, avg_weight: float, owner_actions: set[str], s: Settings) -> float:
    heat_norm = min(heat, HEAT_CAP) / HEAT_CAP
    final = s.w_llm * llm_score / 10 + s.w_heat * heat_norm + s.w_src * src_weight_norm(avg_weight)
    if "down" in owner_actions:
        final -= 0.15
    if "up" in owner_actions:
        final += 0.1
    return final


def is_early_signal(heat: int, llm_score: float, kinds: list[str]) -> bool:
    """New and not yet picked up by the channels: few sources, high score, seen on HN/arXiv/HF/GitHub, not in Telegram."""
    return heat <= 2 and llm_score >= 7 and bool(set(kinds) & EARLY_KINDS) and "telegram" not in kinds


def parse(answer, story_ids: set[int]) -> dict[int, tuple[float, str]]:
    """{story_id: (score 0–10, reason)} for the stories that were asked about."""
    if isinstance(answer, dict):
        answer = [answer]
    if not isinstance(answer, list):
        raise ValueError(f"score: expected a JSON array, got {type(answer).__name__}")
    out = {}
    for r in answer:
        if not isinstance(r, dict) or r.get("story_id") not in story_ids:
            continue
        sc = r.get("score")
        if isinstance(sc, bool) or not isinstance(sc, (int, float)):
            continue
        reason = r.get("reason")
        out[r["story_id"]] = (min(max(float(sc), 0.0), 10.0), " ".join(reason.split()) if isinstance(reason, str) else "")
    return out


async def run(client: AsyncOpenAI, s: Settings, usage: Usage, profile_text: str, inputs: list[dict],
              batch: int = 15) -> tuple[dict[int, tuple[float, str]], int]:
    """(scores, failed batches). A batch that fails twice is skipped: its stories stay unscored."""
    prompt = load_prompt("score")
    system = render(prompt.system, profile_text=profile_text)
    done, failed = {}, 0
    for i in range(0, len(inputs), batch):
        chunk = inputs[i:i + batch]
        try:
            answer = await complete_json(client, s, s.cheap_model, system, json.dumps(chunk, ensure_ascii=False), usage)
            done |= parse(answer, {x["story_id"] for x in chunk})
        except Exception:
            log.exception("score batch %s failed", [x["story_id"] for x in chunk])
            failed += 1
    return done, failed
