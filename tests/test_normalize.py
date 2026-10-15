import asyncio

import httpx
import pytest

from core.normalize import canonical_url, canonical_urls, is_short


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
        ("https://twitter.com/karpathy/status/123?s=20", "https://x.com/karpathy/status/123?s=20"),
        ("https://mobile.twitter.com/karpathy/status/123", "https://x.com/karpathy/status/123"),
        ("https://www.x.com/karpathy/status/123", "https://x.com/karpathy/status/123"),
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
