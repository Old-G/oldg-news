from datetime import date

import pytest

from core.article import (
    Meta, materials, parse_article, parse_overview, parse_weekly, primary_kind, render_article, render_card,
    render_overview, render_weekly, week_label,
)


@pytest.mark.parametrize("url, kind", [
    ("https://github.com/vllm-project/vllm", ("github", "vllm-project/vllm")),
    ("https://x.com/i/status/123", ("x", "123")),
    ("https://arxiv.org/abs/2510.01234", ("arxiv", "2510.01234")),
    ("https://blog.example.com/post", ("page", "https://blog.example.com/post")),
    ("https://t.me/chan/5", None),
    ("https://example.com/", None),
    ("https://example.com/paper.pdf", None),
])
def test_primary_kind(url, kind):
    assert primary_kind(url) == kind


def test_materials_main_link_first_no_repeats_and_capped():
    items = [
        {"title": "Пост", "text": "Пост про модель", "url": None, "tg_link": "https://t.me/a/1", "kind": "telegram"},
        {"title": None, "text": "  пост про   МОДЕЛЬ ", "url": None, "tg_link": "https://t.me/b/2", "kind": "telegram"},
        {"title": "Full", "text": "Full article  text", "url": "https://x.dev/a", "tg_link": None, "kind": "hn"},
    ]
    out = materials(("https://x.dev/a", "Full article text"), items, cap=10_000)
    assert out.startswith("[main link] https://x.dev/a\nFull article text")
    assert out.count("[telegram]") == 1 and "[hn]" not in out  # the HN item repeats the main text
    assert materials(None, [{"title": "t", "text": "w " * 5000, "kind": "rss"}], cap=500).endswith("[…]")
    assert len(materials(None, [{"title": "t", "text": "w " * 5000, "kind": "rss"}], cap=500)) <= 504


ARTICLE = {"title": "Anthropic открыла <MCP> 2", "lead": "Что случилось.", "details": ["Деталь 1", "Деталь & 2"],
           "why_it_matters": "Почему.", "try": None, "caveats": "Цена не указана."}


def test_parse_article_requires_the_core_and_cleans_fields():
    a = parse_article({**ARTICLE, "details": ["  x ", "", 3], "try": "  "})
    assert a["details"] == ["x"] and a["try"] is None
    with pytest.raises(ValueError):
        parse_article({**ARTICLE, "lead": ""})
    with pytest.raises(ValueError):
        parse_article(["not", "an", "object"])


def test_render_article_matches_the_spec_format():
    m = Meta(1, 4, ["agents", "coding-agents"], ["https://www.anthropic.com/news/x", "https://t.me/chan/5"], "release")
    assert render_article(parse_article(ARTICLE), m) == (
        "🚀 <b>Anthropic открыла &lt;MCP&gt; 2</b>\n<i>#1 · 🔥 4 источника · #agents #coding_agents</i>\n\n"
        "Что случилось.\n\n"
        "💡 Почему.\n\n"
        "<blockquote expandable>▸ Деталь 1\n▸ Деталь &amp; 2\n\n⚠️ <b>Оговорки:</b> Цена не указана.</blockquote>\n"
        '🔗 <a href="https://www.anthropic.com/news/x">anthropic.com</a> · <a href="https://t.me/chan/5">t.me/chan</a>'
    )


def test_render_article_drops_details_to_fit_a_message():
    a = {**ARTICLE, "details": ["д" * 1500, "е" * 1500, "ж" * 1500]}
    text = render_article(a, Meta(2, 1, [], []))
    assert len(text) <= 4096 and "е" * 1500 in text and "ж" * 1500 not in text


def test_render_card_fallback():
    assert render_card("T", "S.", "W.", Meta(3, 1, [], ["https://x.dev/a"])) == (
        '📰 <b>T</b>\n<i>#3</i>\n\nS.\n\n💡 W.\n\n🔗 <a href="https://x.dev/a">x.dev</a>')


def test_render_overview():
    ov = parse_overview({"summary": "Сводка.", "direction": "Тренд."})
    text = render_overview(date(2026, 10, 16), ov, ["Первая", "Вторая & третья"], ["3–5 — в 13:00", "🌱 ранние сигналы — в 19:00"])
    assert text == ("☕️ <b>Главное за 16 октября</b>\n\nСводка.\n\n🧭 <b>Куда движемся:</b> Тренд.\n\n"
                    "<b>В выпуске</b>\n1️⃣ Первая\n2️⃣ Вторая &amp; третья\n\n"
                    "<i>⏰ 3–5 — в 13:00 · 🌱 ранние сигналы — в 19:00</i>")
    assert render_overview(date(2026, 10, 16), None, ["Одна"], []) == "☕️ <b>Главное за 16 октября</b>\n\n<b>В выпуске</b>\n1️⃣ Одна"
    with pytest.raises(ValueError):
        parse_overview({"summary": "only"})


def test_weekly():
    assert week_label(date(2026, 10, 12), date(2026, 10, 18)) == "12–18 окт"
    assert week_label(date(2026, 9, 28), date(2026, 10, 4)) == "28 сен – 4 окт"
    w = parse_weekly({"intro": "Неделя.", "watch": None, "trends": [
        {"name": "Агенты", "evidence": "A и B.", "direction": "Дальше.", "try": "Проверить."}, {"name": "", "evidence": "x"}]})
    assert render_weekly(date(2026, 10, 12), date(2026, 10, 18), w) == (
        "🗓 <b>Неделя: куда движемся</b>\n<i>12–18 окт</i>\n\nНеделя.\n\n1️⃣ <b>Агенты</b>\nA и B.\n🧭 Дальше.\n🧪 Проверить.")
    with pytest.raises(ValueError):
        parse_weekly({"trends": []})
