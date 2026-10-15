from datetime import date

import pytest

from bot.keyboards import feedback_keyboard, parse
from core.digest import (
    Candidate, StoryView, buttons, header, owner_may, render_story, select, snippet, view_from_card, wave_of,
)
from core.normalize import is_specific_url


def c(i, sim, heat=1, kinds=("telegram",), manual=False):
    return Candidate(i, sim, heat, list(kinds), manual)


def test_select_ranks_by_taste_with_a_heat_bonus():
    picked = select([c(1, 0.70), c(2, 0.62, heat=3), c(3, 0.65)], n=2, min_sim=0.3, heat_bonus=0.05)
    assert [x.story_id for x in picked] == [2, 1]  # 0.62 + 2·0.05 = 0.72 beats 0.70


def test_lonely_paper_below_min_sim_is_cut_but_a_discussed_one_stays():
    cands = [c(1, 0.25, kinds=("arxiv",)), c(2, 0.25, heat=2, kinds=("arxiv", "telegram")), c(3, 0.25)]
    assert {x.story_id for x in select(cands, 5, 0.3, 0.05)} == {2, 3}


def test_manual_stories_go_first_out_of_turn():
    picked = select([c(1, 0.9), c(2, 0.1, manual=True), c(3, 0.8)], n=2, min_sim=0.3, heat_bonus=0.05)
    assert [x.story_id for x in picked] == [2, 1, 3]


@pytest.mark.parametrize("pos, section, wave", [(1, "top", 1), (3, "top", 1), (4, "top", 2), (6, "top", 2), (1, "early", 3)])
def test_waves(pos, section, wave):
    assert wave_of(pos, section, wave1_n=3) == wave


def test_header():
    assert header(date(2026, 10, 10), 6, 2) == "Radar · 10 окт · 6 историй + 2 сигнала"
    assert header(date(2026, 1, 1), 1, 0) == "Radar · 1 янв · 1 история"
    assert header(date(2026, 3, 22), 3, 1) == "Radar · 22 мар · 3 истории + 1 сигнал"


def test_render_before_enrich_uses_snippet():
    msg = render_story(StoryView(1, "Gemini 4 <Argon>", 8, ["https://blog.google/x", "https://t.me/a/1"],
                                 snippet="Google выпустила новую модель & открыла доступ"))
    assert msg == ("1 · 🔥 heat 8\n<b>Gemini 4 &lt;Argon&gt;</b>\nGoogle выпустила новую модель &amp; открыла доступ\n"
                   "https://blog.google/x\nhttps://t.me/a/1")


def test_render_after_enrich_matches_the_spec_format():
    msg = render_story(StoryView(1, "Agents that remember", 7, ["https://arxiv.org/abs/1"], summary="S.",
                                 why_it_matters="W.", category="paper", tags=["agents", "memory"]))
    assert msg.startswith("1 · 🔥 heat 7 · paper · agents, memory\n<b>Agents that remember</b>\nS.\n<i>Почему важно:</i> W.")


def test_render_stays_within_600_chars():
    msg = render_story(StoryView(2, "t" * 300, 1, ["https://example.com/" + "a" * 80], snippet="слово " * 300))
    assert len(msg) <= 600 and msg.endswith("a" * 80)


def test_snippet_drops_links_and_title():
    assert snippet("Title\nhttps://x.dev/a\nBody line one.\nBody two", "Title") == "Body line one. Body two"
    assert snippet("https://x.dev", None) is None


@pytest.mark.parametrize(
    "done, labels",
    [
        (set(), ["👍", "👎", "⭐ пост"]),
        ({"up"}, ["✓ 👍", "⭐ пост"]),
        ({"down"}, ["✓ 👎"]),
        ({"star"}, ["✓ ⭐"]),
        ({"up", "star"}, ["✓ 👍 ⭐"]),
    ],
)
def test_buttons_follow_the_owner_state(done, labels):
    assert [label for label, _ in buttons(done)] == labels


def test_star_after_up_allowed_reverse_not():
    assert owner_may("star", {"up"})
    assert not owner_may("up", {"star"})
    assert not owner_may("down", {"up"})
    assert not owner_may("noop", set())


def test_view_prefers_article_links_then_the_telegram_post():
    card = {"title": None, "heat": 2, "canonical_url": None, "summary": None, "why_it_matters": None,
            "category": None, "tags": [],
            "items": [{"title": "Пост", "text": "Пост\nGoogle выпустила модель https://blog.google/x", "url": "https://blog.google/x",
                       "urls": ["https://t.me/addlist/abc", "https://blog.google/x"], "tg_link": "https://t.me/chan/5", "kind": "telegram"}]}
    v = view_from_card(card, 1, is_specific_url)
    assert v.urls == ["https://blog.google/x", "https://t.me/chan/5"]
    assert (v.title, v.snippet) == ("Пост", "Google выпустила модель")


def test_callback_data_roundtrip():
    kb = feedback_keyboard(42)
    assert [b.callback_data for b in kb.inline_keyboard[0]] == ["fb:up:42", "fb:down:42", "fb:star:42"]
    assert parse("fb:star:42") == ("star", 42)
    assert parse("garbage") is None
