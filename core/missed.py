"""/missed: where the pipeline lost a story the owner found elsewhere (spec: «Профиль вкуса», missed).

Pure function: the caller looks the story up and passes what it found.
"""

from dataclasses import dataclass
from datetime import date

REASONS = {
    "not_in_db": "этого не было ни в одном источнике",
    "in_digest": "история была в дайджесте",
    "down": "ты сам поставил ей 👎",
    "not_yet": "она уже собрана, но отбора с тех пор ещё не было — в очереди",
    "no_digest": "в эти дни дайджест не собирался",
    "prefilter": "отсёк prefilter: мало похожа на твой вкус",
    "score": "прошла prefilter, но LLM оценила ниже отобранных",
    "digest_limit": "оценка проходила, но не влезла в лимит дайджеста или категории",
}


@dataclass
class Found:
    status: str
    enriched: bool
    prefilter_sim: float | None
    llm_score: float | None
    final_score: float | None
    digest_date: date | None = None       # it was in this digest
    digests_since: int = 0                # digests built while it was a candidate
    cutoff: float | None = None           # lowest final_score picked into those digests
    owner_down: bool = False


def cut_reason(found: Found | None) -> str:
    if found is None:
        return "not_in_db"
    if found.digest_date:
        return "in_digest"
    if found.owner_down:
        return "down"
    if not found.digests_since:
        return "not_yet" if found.status == "new" else "no_digest"
    if not found.enriched:
        return "prefilter"
    if found.final_score is None or found.cutoff is None or found.final_score < found.cutoff:
        return "score"
    return "digest_limit"
