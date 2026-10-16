import pytest

from config import Settings
from core import enrich, score
from core.digest import Candidate


def settings() -> Settings:
    return Settings(tg_api_id=1, tg_api_hash="x", database_url="postgresql://x")


def c(i, sim, heat=1, kinds=("telegram",), manual=False, notes=False):
    return Candidate(i, sim, heat, list(kinds), manual=manual, notes=notes)


def test_prefilter_top_n_plus_hot_manual_and_notes():
    cands = [c(1, 0.9), c(2, 0.8), c(3, 0.1, heat=3), c(4, 0.05, manual=True), c(5, 0.05, notes=True), c(6, 0.2)]
    assert [x.story_id for x in score.prefilter(cands, n=2, min_sim=0.3, heat_bypass=3)] == [1, 2, 3, 4, 5]


def test_prefilter_cuts_lonely_papers_below_min_sim_even_inside_top_n():
    cands = [c(1, 0.25, kinds=("arxiv",)), c(2, 0.25, heat=2, kinds=("arxiv", "telegram")), c(3, 0.2)]
    assert [x.story_id for x in score.prefilter(cands, n=5, min_sim=0.3, heat_bypass=3)] == [2, 3]


def test_prefilter_early_quota_takes_the_best_possible_early_signals_beyond_top_n():
    cands = [c(1, 0.9), c(2, 0.8, kinds=("hn",)), c(3, 0.7, kinds=("arxiv",)), c(4, 0.6, kinds=("github_search",)),
             c(5, 0.65, kinds=("hn", "telegram")), c(6, 0.62, heat=2, kinds=("hf_trending",)), c(7, 0.5)]
    # top 2 by similarity; then the next 2 that may be early (not 5: already in Telegram) — 3 and 6, not 4
    assert [x.story_id for x in score.prefilter(cands, n=2, min_sim=0.3, heat_bypass=3, early_n=2)] == [1, 2, 3, 6]


def test_final_formula():
    s = settings()
    # 0.6·8/10 + 0.25·4/8 + 0.15·(1.0−0.5)
    assert score.final_score(8, 4, 1.0, set(), s) == pytest.approx(0.48 + 0.125 + 0.075)
    assert score.final_score(10, 20, 1.5, set(), s) == pytest.approx(1.0)  # heat capped at 8, weight at 1
    assert score.final_score(0, 1, 0.3, set(), s) == pytest.approx(0.25 / 8)  # weight below 0.5 → 0
    base = score.final_score(5, 1, 1.0, set(), s)
    assert score.final_score(5, 1, 1.0, {"down"}, s) == pytest.approx(base - 0.15)
    assert score.final_score(5, 1, 1.0, {"up"}, s) == pytest.approx(base + 0.1)


@pytest.mark.parametrize("heat, llm, kinds, early", [
    (1, 7, ["hn"], True),
    (2, 9, ["arxiv", "github"], True),
    (2, 9, ["hn", "rss"], True),
    (2, 9, ["hn", "telegram"], False),  # already in the channels
    (3, 9, ["hn"], False),
    (1, 6.9, ["hf_papers"], False),
    (1, 9, ["rss"], False),
])
def test_early_signal(heat, llm, kinds, early):
    assert score.is_early_signal(heat, llm, kinds) is early


def test_score_parse_clamps_and_drops_strangers():
    answer = [{"story_id": 1, "score": 11, "reason": " on  profile "}, {"story_id": 2, "score": "7"},
              {"story_id": 3, "score": -1}, {"story_id": 99, "score": 5}, {"story_id": 4, "score": True}, "junk"]
    assert score.parse(answer, {1, 2, 3, 4}) == {1: (10.0, "on profile"), 3: (0.0, "")}


def test_score_parse_rejects_a_non_array():
    with pytest.raises(ValueError):
        score.parse("text", {1})


def test_enrich_parse_normalizes_fields():
    answer = [
        {"story_id": 1, "title_clean": "T" * 100, "summary": "What  it is.", "why_it_matters": "Why.",
         "category": "Paper", "tags": ["Agents", "agents", "MCP", 5, "", "evals", "rag", "llm"], "is_ad": False,
         "confidence": 0.8},
        {"story_id": 2, "title_clean": "Ad", "summary": "Course.", "category": "marketing", "tags": "x",
         "is_ad": True},
        {"story_id": 3, "title_clean": "", "summary": "no title"},
        {"story_id": 77, "title_clean": "Stranger", "summary": "s"},
    ]
    r1, r2 = enrich.parse(answer, {1, 2, 3})
    assert len(r1.title_clean) == 90 and r1.title_clean.endswith("…")
    assert (r1.summary, r1.category, r1.tags, r1.is_ad) == ("What it is.", "paper", ["agents", "mcp", "evals", "rag", "llm"], False)
    assert (r2.category, r2.tags, r2.is_ad, r2.confidence) == ("other", [], True, None)


def test_enrich_input_caps_text():
    x = enrich.story_input(5, None, "a" * 3000, ["https://x.dev"], 2, ["hn"], 2000)
    assert x == {"story_id": 5, "title": "", "text": "a" * 2000, "urls": ["https://x.dev"], "n_sources": 2,
                 "source_kinds": ["hn"]}
