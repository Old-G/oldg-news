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
EARLY_KINDS = {"hn", "hf_papers", "arxiv", "github", "github_search", "hf_trending"}
HEAT_CAP = 8


def prefilter(cands: list, n: int, min_sim: float, heat_bypass: int, early_n: int = 0) -> list:
    """Spec step 5: top n by prefilter_sim, plus the next early_n by prefilter_sim that may become early signals,
    plus every story with heat ≥ heat_bypass, plus manual and notes. A paper nobody else picked up and below
    min_sim is cut first. The early quota exists because a one-source find rarely makes the top n by similarity."""
    keep = [c for c in cands if not (set(c.kinds) <= PAPER_KINDS and c.heat == 1 and c.prefilter_sim < min_sim)]
    ranked = sorted(keep, key=lambda c: c.prefilter_sim, reverse=True)
    early = [c for c in ranked[n:] if may_be_early(c.heat, c.kinds)][:early_n]
    top = {c.story_id for c in ranked[:n] + early}
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


def may_be_early(heat: int, kinds: list[str]) -> bool:
    """New and not yet picked up by the channels: few sources, seen on HN/arXiv/HF/GitHub, not in Telegram."""
    return heat <= 2 and bool(set(kinds) & EARLY_KINDS) and "telegram" not in kinds


def is_early_signal(heat: int, llm_score: float, kinds: list[str]) -> bool:
    return may_be_early(heat, kinds) and llm_score >= 7


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
