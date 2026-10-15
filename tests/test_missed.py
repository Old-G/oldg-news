from datetime import date

import pytest

from core.missed import REASONS, Found, cut_reason


def f(**kw) -> Found:
    base = dict(status="new", enriched=True, prefilter_sim=0.5, llm_score=7, final_score=0.6, digests_since=1, cutoff=0.5)
    return Found(**{**base, **kw})


@pytest.mark.parametrize("found, reason", [
    (None, "not_in_db"),
    (f(status="digested", digest_date=date(2026, 10, 16)), "in_digest"),
    (f(status="dismissed", owner_down=True), "down"),
    (f(digests_since=0), "not_yet"),
    (f(status="dismissed", digests_since=0), "no_digest"),
    (f(enriched=False, llm_score=None, final_score=None), "prefilter"),
    (f(final_score=0.4), "score"),
    (f(llm_score=None, final_score=None), "score"),
    (f(final_score=0.55), "digest_limit"),
])
def test_cut_reason(found, reason):
    assert cut_reason(found) == reason
    assert reason in REASONS
