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
