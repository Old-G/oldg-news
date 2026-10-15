import asyncio

import httpx
import pytest

from core.normalize import canonical_url, canonical_urls, clean_text, detect_lang, is_ad, is_short, primary_url


@pytest.mark.parametrize(
    "raw, expected",
    [
        # tracking params go, meaningful ones stay
        ("https://example.com/post?utm_source=tg&utm_medium=x&id=5", "https://example.com/post?id=5"),
        ("https://open.spotify.com/x?si=abc&ref=hn&fbclid=1", "https://open.spotify.com/x"),
        # arXiv: pdf, versions, html → abs
        ("https://arxiv.org/pdf/2510.01234v2", "https://arxiv.org/abs/2510.01234"),
        ("http://arxiv.org/pdf/2510.01234v2.pdf", "https://arxiv.org/abs/2510.01234"),
        ("https://arxiv.org/abs/2510.01234v3", "https://arxiv.org/abs/2510.01234"),
        ("https://www.arxiv.org/html/2510.01234v1", "https://arxiv.org/abs/2510.01234"),
        # HF papers point to the same paper
        ("https://huggingface.co/papers/2510.01234", "https://arxiv.org/abs/2510.01234"),
        # GitHub: anything inside a repo → the repo
        ("https://github.com/org/repo/blob/main/README.md", "https://github.com/org/repo"),
        ("https://github.com/org/repo.git", "https://github.com/org/repo"),
        ("https://github.com/org/repo/releases/tag/v1?tab=x", "https://github.com/org/repo"),
        ("https://github.com/topics/llm", "https://github.com/topics/llm"),
        ("https://github.com/org", "https://github.com/org"),
        # one domain for X
        ("https://twitter.com/karpathy/status/123?s=20", "https://x.com/i/status/123"),
        ("https://mobile.twitter.com/karpathy/status/123", "https://x.com/i/status/123"),
        ("https://www.x.com/someone_else/status/123/photo/1", "https://x.com/i/status/123"),
        ("https://x.com/karpathy", "https://x.com/karpathy"),
        ("https://youtu.be/abc123?t=10", "https://youtube.com/watch?v=abc123&t=10"),
        # cosmetic: scheme, www, fragment, trailing slash, missing scheme
        ("http://www.Example.com/a/#section", "https://example.com/a"),
        ("example.com/x/", "https://example.com/x"),
    ],
)
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


def test_canonical_url_is_idempotent():
    for u in ["https://arxiv.org/pdf/2510.01234v2", "https://github.com/a/b/tree/x", "https://twitter.com/a/status/1"]:
        assert canonical_url(canonical_url(u)) == canonical_url(u)


def test_is_short():
    assert is_short("https://t.co/abc")
    assert is_short("bit.ly/xyz")
    assert is_short("https://readhacker.news/s/6PHsP")
    assert not is_short("https://github.com/a/b")


def test_short_links_are_expanded_then_canonicalised():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "t.co":
            return httpx.Response(301, headers={"location": "https://arxiv.org/pdf/2510.01234v2?utm_source=x"})
        return httpx.Response(200)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await canonical_urls(["https://t.co/abc", "https://arxiv.org/abs/2510.01234"], client)

    assert asyncio.run(run()) == ["https://arxiv.org/abs/2510.01234"]


def test_failed_expansion_keeps_the_link():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await canonical_urls(["https://bit.ly/x"], client)

    assert asyncio.run(run()) == ["https://bit.ly/x"]


def test_primary_url_skips_telegram_and_site_roots():
    assert primary_url(["https://t.me/addlist/abc", "https://example.com", "https://github.com/a/b"]) == "https://github.com/a/b"
    assert primary_url(["https://t.me/chan/123", "https://t.me/chan"]) is None
    assert primary_url([]) is None


def test_clean_text_drops_junk_lines():
    text = "Вышел Claude Code 3.0\nДетали: агенты в фоне\n🔥🔥🔥\n#ai #news\n👉 Подписывайтесь на канал\n@some_channel"
    assert clean_text(text) == "Вышел Claude Code 3.0\nДетали: агенты в фоне"
    assert clean_text("") == ""
    assert clean_text("🔥") is None


@pytest.mark.parametrize(
    "text, urls, own, expected",
    [
        ("Новая модель Qwen", ["https://huggingface.co/qwen"], "chan", False),
        ("Скидка по промокоду AI20", ["https://shop.example/x"], "chan", True),
        ("Реклама. erid: 2Vtzqx", [], "chan", True),
        ("Лучший канал про нейросети, подпишись", ["https://t.me/other_channel"], "chan", True),
        ("Вступай: https://t.me/+AbCd", ["https://t.me/+AbCd"], "chan", True),
        ("Новость дня, подробности ниже", ["https://t.me/chan"], "chan", False),       # own footer
        ("Смотрите пост", ["https://t.me/other_channel/123"], "chan", False),          # a post, not a channel
        ("Текст без ссылок", [], "chan", False),
    ],
)
def test_ad_heuristics(text, urls, own, expected):
    assert is_ad(text, urls, "telegram", own) is expected


def test_ads_only_judged_for_telegram():
    assert is_ad("промокод", [], "rss") is False


def test_detect_lang():
    assert detect_lang("Вышла новая модель для агентов с длинным контекстом") == "ru"
    assert detect_lang("Anthropic released a new version of the MCP specification") == "en"
    assert detect_lang("🔥🔥🔥") == "und"
    assert detect_lang(None) == "und"


def test_medium_rss_tracking_and_tail_are_removed():
    assert canonical_url("https://pub.towardsai.net/stop-x-ff828a178f52?source=rss----98111c9905da---4") == \
        "https://pub.towardsai.net/stop-x-ff828a178f52"
    assert canonical_url("https://example.com/a?source=newsletter") == "https://example.com/a?source=newsletter"
    for tail in ("email-fb4a25e08c9f-1792021759698-digest.reader-5517fd7b58a6", "home_for_you---------0-99----",
                 "post_page-----b7b753f52f32---------------------------------------"):
        assert canonical_url(f"https://medium.com/@a/b-b7b753f52f32?source={tail}") == "https://medium.com/@a/b-b7b753f52f32"
    assert clean_text("From plan mode to hooks, these patterns help… Continue reading on Towards AI »") == \
        "From plan mode to hooks, these patterns help…"
