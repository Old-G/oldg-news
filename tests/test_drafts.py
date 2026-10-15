import pytest

from core.drafts import clean_ru, parse_en, story_record
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
