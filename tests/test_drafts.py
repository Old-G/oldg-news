import pytest

from core.drafts import clean_ru, parse_en, share_button, story_record
from core.llm import load_prompt


CARD = {"title": "Scion replaces git at Anthropic", "summary": "S.", "why_it_matters": "W.", "category": "infra",
        "tags": ["infra", "agents"], "heat": 4, "article": {"title": "RU"}, "items": [{"text": "raw"}]}


def test_story_record_is_the_english_story_without_the_ru_article():
    rec = story_record(CARD, ["https://www.anthropic.com/x", "https://t.me/chan/5", "https://a.dev/1",
                              "https://b.dev/2", "https://c.dev/3"])
    assert rec == {"title": "Scion replaces git at Anthropic", "summary": "S.", "why_it_matters": "W.",
                   "category": "infra", "tags": ["infra", "agents"], "n_sources": 4,
                   "links": ["https://www.anthropic.com/x", "https://a.dev/1", "https://b.dev/2"]}
    assert story_record(CARD, ["https://t.me/chan/5"])["links"] == ["https://t.me/chan/5"]  # Telegram-only story


def test_clean_ru():
    assert clean_ru("```\nПост.\nhttps://x.dev\n```") == "Пост.\nhttps://x.dev"
    assert clean_ru("  Пост `code`  ") == "Пост `code`"
    with pytest.raises(ValueError):
        clean_ru("  ")


def test_parse_en():
    assert parse_en({"x": " X post ", "linkedin": "LI post"}) == ("X post", "LI post")
    for bad in ({"x": "only"}, {"x": "", "linkedin": "l"}, ["x", "linkedin"]):
        with pytest.raises(ValueError):
            parse_en(bad)


@pytest.mark.parametrize("name", ["draft_ru", "draft_en"])
def test_draft_prompts_take_the_record_and_the_excerpt(name):
    import re
    p = load_prompt(name)
    assert set(re.findall(r"\{(\w+)\}", p.system + p.user)) == {"story_record_json", "best_item_text"}


def test_share_button_opens_the_compose_window_with_the_whole_text():
    label, url = share_button("x", "Agents & MCP: 2× faster?\nhttps://a.dev/1")
    assert label == "Открыть в X"
    assert url == "https://x.com/intent/post?text=Agents%20%26%20MCP%3A%202%C3%97%20faster%3F%0Ahttps%3A%2F%2Fa.dev%2F1"
    assert share_button("linkedin", "Hi") is None  # published through the API
    assert share_button("tg", "Привет") is None
