from datetime import datetime, timezone

import numpy as np
import pytest

from core.llm import load_prompt, render
from core.taste import NoteItem, format_item, kmeans


def test_taste_prompt_loads_with_its_placeholders():
    p = load_prompt("taste_profile")
    assert p.system.startswith("You are building a reading-preference profile")
    assert p.user.startswith("POSITIVE ({n_pos} items, newest first):")
    out = render(p.user, n_pos=2, positive_items="- a\n- b", n_neg=0, negative_items="(none yet)")
    assert "POSITIVE (2 items" in out and "(none yet)" in out and "{" not in out


def test_render_keeps_json_braces_and_rejects_missing_values():
    assert render('{"story_id": 1} {x}', x=5) == '{"story_id": 1} 5'
    with pytest.raises(KeyError):
        render("{a} {b}", a=1)


def test_kmeans_finds_separated_groups():
    rng = np.random.default_rng(1)
    a = np.array([1.0, 0, 0]) + rng.normal(0, 0.05, (40, 3))
    b = np.array([0, 1.0, 0]) + rng.normal(0, 0.05, (25, 3))
    centers, sizes = kmeans(np.vstack([a, b]), k=2)
    assert sorted(sizes.tolist()) == [25, 40]
    assert np.allclose(np.linalg.norm(centers, axis=1), 1)
    assert {int(np.argmax(c)) for c in centers} == {0, 1}


def test_kmeans_k_larger_than_data():
    centers, sizes = kmeans(np.eye(3), k=8)
    assert len(centers) == 3 and sizes.sum() == 3


def test_format_item_for_the_prompt():
    it = NoteItem(datetime(2026, 9, 29, tzinfo=timezone.utc), "Hindsight. Новая агентная память.",
                  "Hindsight. Новая агентная память.\nУ проекта 42k звёзд https://github.com/x/y\nВ чём проблема\nещё\nи ещё",
                  "https://github.com/vectorize-io/hindsight", True)
    assert format_item(it) == (
        "- 2026-09-29 | Hindsight. Новая агентная память. | forwarded post | github.com\n"
        "  У проекта 42k звёзд\n  В чём проблема\n  ещё"
    )


def test_signal_label_replaces_the_notes_kind():
    from core.taste import SIGNAL_LABELS
    it = NoteItem(datetime(2026, 10, 15, tzinfo=timezone.utc), "Scion", "Anthropic moved off git", "https://anthropic.com/x",
                  False, SIGNAL_LABELS[("star", "bot")])
    assert format_item(it).startswith("- 2026-10-15 | Scion | starred in his digest | anthropic.com")


def test_neg_k_waits_for_enough_downvotes():
    from core.taste import neg_k
    assert (neg_k(0, 3), neg_k(2, 3), neg_k(3, 3), neg_k(40, 3)) == (0, 0, 3, 3)


def test_profile_diff_and_message():
    from core.taste import diff_message, profile_diff
    d = profile_diff("1. Core\n- agents\n- rag\n", "1. Core\n- agents & memory\n- rag\n")
    assert d == ["− - agents", "+ - agents & memory"]
    built = {"signals": {"notes": 89, "star": 2}, "positive": 91, "negative": 0, "k_pos": 8, "k_neg": 0,
             "new_positive": 7, "new_negative": 0}
    msg = diff_message(1, 2, built, d, {"prefilter": 2, "not_in_db": 1}, activated=True)
    assert msg.startswith("🧭 <b>Профиль вкуса v2</b> (был v1)\nПозитив: 91 (заметки 89, ⭐ 2, 👍 0, missed 0) · 👎 0")
    assert "Missed за неделю: prefilter 2 · not_in_db 1" in msg
    assert "<blockquote expandable>− - agents\n+ - agents &amp; memory</blockquote>" in msg
    assert msg.endswith("--activate 1</code>")
    long = diff_message(1, 2, built, ["+ " + "x" * 100] * 100, {}, activated=False)
    assert len(long) <= 4096 and "--activate 2" in long


def test_update_without_new_signals_keeps_the_text_and_makes_no_call():
    import asyncio
    from core.taste import update_profile
    from core.llm import Usage, load_prompt
    import re as _re

    class NoCall:
        responses = None  # any call would fail

    assert asyncio.run(update_profile(NoCall(), None, "PROFILE", 1, "2026-10-11", [], [], Usage())) == "PROFILE"
    p = load_prompt("taste_update")
    assert set(_re.findall(r"\{(\w+)\}", p.system + p.user)) == {
        "version", "profile_text", "since", "n_pos", "positive_items", "n_neg", "negative_items"}
