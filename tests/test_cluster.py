from core.cluster import decide
from core.embed import embed_input

SIM, URL_MIN = 0.86, 0.5


def test_shared_url_glues_when_the_story_is_about_the_same_thing():
    d = decide(url_match=(7, 0.72), neighbors=[(9, 0.90)], cluster_sim=SIM, url_min_sim=URL_MIN)
    assert (d.story_id, d.reason) == (7, "url")


def test_shared_url_alone_does_not_glue_unrelated_posts():
    # two different Claude Code tips both linking the claude-code repo
    d = decide(url_match=(7, 0.31), neighbors=[(7, 0.31)], cluster_sim=SIM, url_min_sim=URL_MIN)
    assert (d.story_id, d.reason) == (None, "new")


def test_nearest_story_above_threshold_joins():
    d = decide(url_match=None, neighbors=[(3, 0.91), (4, 0.88)], cluster_sim=SIM, url_min_sim=URL_MIN)
    assert (d.story_id, d.reason) == (3, "sim")


def test_threshold_is_inclusive():
    assert decide(None, [(3, 0.86)], SIM, URL_MIN).story_id == 3


def test_below_threshold_starts_a_new_story():
    d = decide(url_match=None, neighbors=[(3, 0.85)], cluster_sim=SIM, url_min_sim=URL_MIN)
    assert (d.story_id, d.reason) == (None, "new")


def test_no_stories_yet():
    assert decide(None, [], SIM, URL_MIN).reason == "new"


def test_embed_input_title_plus_trimmed_text():
    assert embed_input("Title", "x" * 2000, None, 1500) == "Title\n" + "x" * 1500
    assert embed_input(None, None, "https://a.dev/p", 1500) == "https://a.dev/p"
    assert embed_input("Only title", None, None, 1500) == "Only title"
