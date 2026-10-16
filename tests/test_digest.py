import pytest

from bot.keyboards import feedback_keyboard, parse
from core.digest import (
    Candidate, all_batches_failed, buttons, later_lines, owner_may, select, story_links, wave_of,
)
from core.normalize import is_specific_url


def c(i, final=None, cat="paper", early=False, manual=False, sim=0.5, heat=1):
    return Candidate(i, sim, heat, ["telegram"], manual=manual, category=cat, final_score=final, is_early=early)


def ids(cands):
    return [x.story_id for x in cands]


def test_select_by_final_score_with_a_cap_per_category():
    cands = [c(1, 0.9), c(2, 0.8), c(3, 0.7), c(4, 0.6, "release"), c(5, 0.5, "tool")]
    top, early = select(cands, n=3, max_per_category=2, early_n=3, heat_bonus=0.05)
    assert ids(top) == [1, 2, 4] and early == []  # the third paper is over the cap


def test_manual_first_out_of_turn_and_early_from_the_rest():
    cands = [c(1, 0.9), c(2, manual=True), c(3, 0.8, "tool", early=True), c(4, 0.7, "release", early=True),
             c(5, 0.6, "agent", early=True), c(6, 0.5, "infra", early=True)]
    top, early = select(cands, n=2, max_per_category=2, early_n=2, heat_bonus=0.05)
    assert ids(top) == [2, 1, 3] and ids(early) == [4, 5]


def test_unscored_stories_only_fill_what_is_left():
    cands = [c(1, None, sim=0.95, heat=4), c(2, 0.1), c(3, None, sim=0.6)]
    top, _ = select(cands, n=2, max_per_category=5, early_n=0, heat_bonus=0.05)
    assert ids(top) == [2, 1]


@pytest.mark.parametrize("pos, section, wave", [(1, "top", 1), (3, "top", 1), (4, "top", 2), (6, "top", 2), (1, "early", 3)])
def test_waves(pos, section, wave):
    assert wave_of(pos, section, wave1_n=3) == wave


def test_later_lines():
    waves = ["07:30", "13:00", "19:00"]
    assert later_lines(6, True, waves, 3) == ["4–6 — в 13:00", "🌱 ранние сигналы — в 19:00"]
    assert later_lines(4, False, waves, 3) == ["4 — в 13:00"]
    assert later_lines(3, False, waves, 3) == []
    assert later_lines(6, True, [], 3) == []


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


def test_links_prefer_the_article_then_the_telegram_post():
    card = {"canonical_url": None,
            "items": [{"url": "https://blog.google/x", "urls": ["https://t.me/addlist/abc", "https://blog.google/x"],
                       "tg_link": "https://t.me/chan/5"}]}
    assert story_links(card, is_specific_url) == ["https://blog.google/x", "https://t.me/chan/5"]


def test_callback_data_roundtrip():
    kb = feedback_keyboard(42)
    assert [b.callback_data for b in kb.inline_keyboard[0]] == ["fb:up:42", "fb:down:42", "fb:star:42"]
    assert parse("fb:star:42") == ("star", 42)
    assert parse("garbage") is None


@pytest.mark.parametrize("n_inputs, failed, held", [
    (25, 3, True),   # 3 batches of 10, all failed: no network to the LLM, the pick waits
    (25, 2, False),  # one batch got through: pick with what there is
    (0, 0, False),   # nothing to do is not a failure
])
def test_all_batches_failed(n_inputs, failed, held):
    assert all_batches_failed(n_inputs, 10, failed) is held
