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


TELEGRAM_HOSTS = {"t.me", "telegram.me", "telegram.dog"}


def is_specific_url(url: str | None) -> bool:
    """A link that names one thing: not a Telegram channel/footer link, not a bare site root.

    Only such links may glue items into one story by exact match; channel footers
    (t.me/addlist/…, t.me/channel) would otherwise merge unrelated posts.
    """
    if not url:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return host not in TELEGRAM_HOSTS and parts.path.strip("/") != ""


def primary_url(urls: list[str]) -> str | None:
    """The item's main link: the first specific one; Telegram-only posts have none."""
    return next((u for u in urls if is_specific_url(u)), None)


# Lines that carry no content: emoji/punctuation only, hashtags/mentions only, «подписывайся» footers
_JUNK_LINE = re.compile(r"^[\W_]*$", re.U)
_TAGS_LINE = re.compile(r"^(?:\s*[#@][\w.]+[\s|·•,]*)+$", re.U)
_SUBSCRIBE = re.compile(r"подпис(?:ывайтесь|ывайся|аться|ка)|subscribe|наш (?:канал|чат)|👉\s*@", re.I)


def clean_text(text: str | None) -> str | None:
    if not text:
        return text
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or _JUNK_LINE.match(s) or _TAGS_LINE.match(s):
            continue
        if len(s) <= 120 and _SUBSCRIBE.search(s):
            continue
        lines.append(s)
    return "\n".join(lines) or None


_AD_MARKERS = re.compile(r"#реклама|#ad\b|#промо|\berid\b|промокод|по промокоду|на правах рекламы|партн[её]рский материал", re.I)
_CHANNEL_LINK = re.compile(r"^https://t\.me/(?:\+|joinchat/|addlist/)?[\w+-]+$")


def is_ad(text: str | None, urls: list[str], kind: str, own_username: str | None = None) -> bool:
    """Telegram ad heuristics from the spec: #реклама, erid, промокод, link only to a channel.

    A channel linking to itself (the usual footer) is not an ad; promoting another channel is.
    """
    if kind != "telegram":
        return False
    if text and _AD_MARKERS.search(text):
        return True
    own = f"https://t.me/{own_username}".lower() if own_username else None
    foreign = [u for u in urls if u.lower() != own]
    return bool(foreign) and all(_CHANNEL_LINK.match(u) for u in foreign)


def detect_lang(text: str | None, min_score: float = 0.3) -> str:
    if not text or not text.strip():
        return "und"
    from fast_langdetect import LangDetectConfig, detect  # loads the bundled model on first use

    # the library cuts input to 80 chars by default (and logs it every time); 500 is plenty and still fast
    best = detect(" ".join(text.split())[:500], config=LangDetectConfig(max_input_length=500, model="lite"), k=1)
    return best[0]["lang"] if best and best[0]["score"] >= min_score else "und"
