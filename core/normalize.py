"""Text cleanup and canonical URLs."""

import asyncio
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

DROP_PARAMS = {"ref", "si", "fbclid"}
SHORT_HOSTS = {
    "t.co", "bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly", "lnkd.in", "is.gd",
    "t.ly", "rebrand.ly", "dlvr.it", "link.medium.com", "readhacker.news",
}
# github.com/<first>/... where <first> is not an owner
GITHUB_NON_OWNERS = {
    "orgs", "topics", "features", "marketplace", "sponsors", "settings", "explore",
    "collections", "trending", "search", "notifications", "about", "pricing", "enterprise",
}
ARXIV_ID = r"(\d{4}\.\d{4,5})(?:v\d+)?"


def content_hash(url: str | None, text: str | None) -> str:
    return hashlib.sha256((url or (text or "")[:500]).encode()).hexdigest()


def canonical_url(url: str) -> str:
    """One spelling per resource, so the same link from two sources is recognised as one."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("mobile.")
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in DROP_PARAMS
    ]

    if host in ("twitter.com", "x.com"):
        host = "x.com"
        if m := re.match(r"^/\w+/status(?:es)?/(\d+)", path):
            # any handle opens the post, so the id alone identifies it
            path, query = f"/i/status/{m.group(1)}", []
    elif host == "youtu.be" and path:
        host, path, query = "youtube.com", "/watch", [("v", path.lstrip("/")), *query]
    elif host in ("arxiv.org", "export.arxiv.org") and (m := re.match(rf"^/(?:abs|pdf|html)/{ARXIV_ID}(?:\.pdf)?$", path)):
        host, path, query = "arxiv.org", f"/abs/{m.group(1)}", []
    elif host == "huggingface.co" and (m := re.match(rf"^/papers/{ARXIV_ID}$", path)):
        host, path, query = "arxiv.org", f"/abs/{m.group(1)}", []
    elif host == "github.com":
        segs = path.strip("/").split("/")
        if len(segs) >= 2 and segs[0].lower() not in GITHUB_NON_OWNERS:
            path, query = "/" + "/".join(segs[:2]).removesuffix(".git"), []

    return urlunsplit(("https", host, path, urlencode(query), ""))


def is_short(url: str) -> bool:
    return (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower().removeprefix("www.") in SHORT_HOSTS


async def expand(url: str, client: httpx.AsyncClient, timeout: float = 5.0) -> str:
    """Resolves a short link with a HEAD request; on any failure keeps the original."""
    if not is_short(url):
        return url
    try:
        r = await client.head(url, follow_redirects=True, timeout=timeout)
        return str(r.url)
    except httpx.HTTPError:
        return url


async def canonical_urls(urls: list[str], client: httpx.AsyncClient, timeout: float = 5.0) -> list[str]:
    expanded = await asyncio.gather(*(expand(u, client, timeout) for u in urls))
    return list(dict.fromkeys(canonical_url(u) for u in expanded))
