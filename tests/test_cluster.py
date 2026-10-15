from core.cluster import decide
from core.embed import embed_input

SIM, URL_MIN, SAME = 0.80, 0.5, 0.98


def d(url_match, neighbors):
    r = decide(url_match, neighbors, cluster_sim=SIM, url_min_sim=URL_MIN, same_source_sim=SAME)
    return r.story_id, r.reason


def test_shared_url_glues_when_the_story_is_about_the_same_thing():
    assert d((7, 0.72, False), [(9, 0.90, False)]) == (7, "url")


def test_shared_url_alone_does_not_glue_unrelated_posts():
    # two different Claude Code tips both linking the claude-code repo
    assert d((7, 0.31, False), [(7, 0.31, False)]) == (None, "new")


def test_nearest_story_above_threshold_joins():
    assert d(None, [(3, 0.84, False), (4, 0.81, False)]) == (3, "sim")


def test_threshold_is_inclusive():
    assert d(None, [(3, 0.80, False)]) == (3, "sim")


def test_below_threshold_starts_a_new_story():
    assert d(None, [(3, 0.79, False)]) == (None, "new")


def test_no_stories_yet():
    assert d(None, []) == (None, "new")


def test_same_channel_daily_template_stays_apart():
    # «Таро на 14 октября» vs «на 15 октября» from one channel: 0.95 alike, different stories
    assert d(None, [(5, 0.95, True)]) == (None, "new")


def test_same_channel_verbatim_repeat_glues():
    assert d(None, [(5, 0.99, True)]) == (5, "sim")


def test_blocked_same_source_neighbor_falls_through_to_next_story():
    assert d(None, [(5, 0.95, True), (6, 0.83, False)]) == (6, "sim")


def test_same_source_rule_applies_to_url_matches_too():
    # two releases of one repo from its releases feed share the repo URL
    assert d((8, 0.88, True), [(8, 0.88, True)]) == (None, "new")


def test_embed_input_title_plus_trimmed_text():
    assert embed_input("Title", "x" * 2000, None, 1500) == "Title\n" + "x" * 1500
    assert embed_input(None, None, "https://a.dev/p", 1500) == "https://a.dev/p"
    assert embed_input("Only title", None, None, 1500) == "Only title"
