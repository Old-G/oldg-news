"""Step 9 of the pipeline: a story's materials → a self-contained Russian mini-article (prompt `article_ru`),
the day's overview (`overview_ru`) and the week's post (`weekly_ru`); their Telegram HTML.

The reader does not open the originals, so the article is written from the full text of the main link
plus every item of the story, not from a one-line summary.
"""

import html
import logging
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

import feedparser
import httpx
import trafilatura

from core.digest import MONTHS, _one_line

log = logging.getLogger(__name__)
MAX_PAGE_BYTES = 3_000_000
MAX_MESSAGE_CHARS = 4096


# --- materials ---------------------------------------------------------------

def primary_kind(url: str) -> tuple[str, str] | None:
    """How to read a link: ('github', 'org/repo'), ('x', id), ('arxiv', id), ('page', url); None to skip."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.strip("/")
    if host in ("t.me", "telegram.me") or not path:
        return None
    if host == "github.com" and len(path.split("/")) == 2:
        return "github", path
    if host in ("x.com", "twitter.com") and (m := re.search(r"status/(\d+)", path)):
        return "x", m.group(1)
    if host == "arxiv.org" and (m := re.match(r"abs/(.+)", path)):
        return "arxiv", m.group(1)
    if path.lower().endswith(".pdf"):
        return None
    return "page", url


async def _get_text(http: httpx.AsyncClient, url: str, **kw) -> str:
    async with http.stream("GET", url, **kw) as r:
        r.raise_for_status()
        body = b""
        async for chunk in r.aiter_bytes():
            body += chunk
            if len(body) > MAX_PAGE_BYTES:
                break
    return body.decode(r.encoding or "utf-8", errors="replace")


async def primary_text(http: httpx.AsyncClient, url: str) -> str | None:
    """Full text behind the story's main link; None when there is nothing readable. Never raises."""
    kind = primary_kind(url)
    if not kind:
        return None
    try:
        match kind:
            case "github", repo:
                return await _get_text(http, f"https://api.github.com/repos/{repo}/readme",
                                       headers={"Accept": "application/vnd.github.raw"})
            case "x", status_id:
                r = await http.get(f"https://api.fxtwitter.com/i/status/{status_id}")
                t = r.json()["tweet"]
                quote = t.get("quote") or {}
                text = f"@{t['author']['screen_name']}: {t['text']}"
                if quote.get("text"):
                    text += f"\n\nQuoted @{quote.get('author', {}).get('screen_name', '?')}: {quote['text']}"
                return text
            case "arxiv", paper_id:
                feed = feedparser.parse(await _get_text(http, f"https://export.arxiv.org/api/query?id_list={paper_id}"))
                e = feed.entries[0] if feed.entries else None
                return f"{e.title}\n\n{e.summary}" if e and e.get("summary") else None
            case "page", page_url:
                page = await _get_text(http, page_url)
                return trafilatura.extract(page, url=page_url, include_comments=False, include_tables=True,
                                           favor_precision=True)
    except Exception as e:
        log.warning("primary text %s failed: %r", url, e)
    return None


def materials(primary: tuple[str, str | None] | None, items: list[dict], cap: int) -> str:
    """SOURCES for the prompt: the main link's text first, then every item, no repeated texts, ≤ cap chars."""
    blocks, seen = [], set()
    if primary and primary[1]:
        blocks.append(f"[main link] {primary[0]}\n{primary[1].strip()}")
        seen.add(_key(primary[1]))
    for it in items:
        body = it.get("text") or it.get("title") or ""
        if not body.strip() or _key(body) in seen:
            continue
        seen.add(_key(body))
        text = body if not it.get("title") or body.startswith(it["title"]) else f"{it['title']}\n{body}"
        where = it.get("url") or it.get("tg_link") or ""
        blocks.append(f"[{it.get('kind', 'item')}] {where}\n{text.strip()}".rstrip())
    out = "\n\n---\n\n".join(blocks)
    return out if len(out) <= cap else out[:cap].rsplit("\n", 1)[0] + "\n[…]"


def _key(text: str) -> str:
    return " ".join(text.lower().split())[:300]


# --- LLM answers ---------------------------------------------------------------

def _str(v) -> str:
    return " ".join(v.split()) if isinstance(v, str) else ""


def _opt(v) -> str | None:
    return _str(v) or None


def parse_article(answer) -> dict:
    """{title, lead, details[], why_it_matters, try, caveats}; ValueError when the core is missing."""
    if not isinstance(answer, dict):
        raise ValueError(f"article: expected an object, got {type(answer).__name__}")
    a = {
        "title": _str(answer.get("title")),
        "lead": _str(answer.get("lead")),
        "details": [_str(d) for d in answer.get("details") or [] if _str(d)] if isinstance(answer.get("details"), list) else [],
        "why_it_matters": _str(answer.get("why_it_matters")),
        "try": _opt(answer.get("try")),
        "caveats": _opt(answer.get("caveats")),
    }
    if not (a["title"] and a["lead"] and a["why_it_matters"]):
        raise ValueError("article: title, lead and why_it_matters are required")
    return a


def parse_overview(answer) -> dict:
    if not isinstance(answer, dict) or not _str(answer.get("summary")) or not _str(answer.get("direction")):
        raise ValueError("overview: summary and direction are required")
    return {"summary": _str(answer["summary"]), "direction": _str(answer["direction"])}


def parse_weekly(answer) -> dict:
    if not isinstance(answer, dict):
        raise ValueError("weekly: expected an object")
    trends = []
    for t in answer.get("trends") or []:
        if isinstance(t, dict) and _str(t.get("name")) and _str(t.get("evidence")):
            trends.append({"name": _str(t["name"]), "evidence": _str(t["evidence"]),
                           "direction": _str(t.get("direction")), "try": _opt(t.get("try"))})
    if not trends:
        raise ValueError("weekly: no trends")
    return {"intro": _str(answer.get("intro")), "trends": trends, "watch": _opt(answer.get("watch"))}


# --- prompt inputs ---------------------------------------------------------------

def overview_stories(articles: list[dict]) -> str:
    return "\n\n".join(
        f"{i}. {a['title']}\n{a['lead']}\nWhy it matters: {a['why_it_matters']}" for i, a in enumerate(articles, 1)
    )


def weekly_stories(rows: list[tuple[dict, set[str]]]) -> str:
    """rows: (article, the author's actions)."""
    out = []
    for a, actions in rows:
        marks = ("★ " if "star" in actions else "") + ("👍 " if "up" in actions else "")
        out.append(f"- {marks}{a['title']}: {a['lead']} Why it matters: {a['why_it_matters']}")
    return "\n".join(out)


def day_label(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}"


def week_label(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS[end.month - 1]}"
    return f"{day_label(start)} – {day_label(end)}"


# --- rendering -----------------------------------------------------------------
# Phone first: the point is visible at once, the details sit in an expandable quote (Bot API 7.4+).

CATEGORY_ICON = {"release": "🚀", "paper": "📄", "tool": "🛠", "infra": "⚙️", "agent": "🤖", "business": "💼"}
KEYCAPS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]


@dataclass
class Meta:
    position: int
    heat: int
    tags: list[str]
    links: list[str]  # main first, then the other specific links, then the Telegram post
    category: str | None = None


def _e(s: str) -> str:
    return html.escape(s, quote=False)


def _source_links(links: list[str]) -> str:
    parts = []
    for u in links[:3]:
        p = urlsplit(u)
        host = (p.hostname or "").removeprefix("www.")
        label = f"{host}/{p.path.strip('/').split('/')[0]}" if host == "t.me" else host
        parts.append(f'<a href="{html.escape(u)}">{_e(label or u)}</a>')
    return "🔗 " + " · ".join(parts) if parts else ""


def _hashtag(tag: str) -> str:
    return "#" + re.sub(r"\W", "_", tag.replace("-", "_"))


def _head(title: str, m: Meta) -> str:
    """Icon + bold title, then a quiet line: number, how many sources, tags as channel hashtags."""
    meta = [f"#{m.position}"]
    if m.heat >= 2:
        meta.append(f"🔥 {m.heat} {_plural(m.heat, 'источник', 'источника', 'источников')}")
    if m.tags:
        meta.append(" ".join(_hashtag(t) for t in m.tags[:3]))
    icon = CATEGORY_ICON.get(m.category or "", "📰")
    return f"{icon} <b>{_e(title)}</b>\n<i>{' · '.join(meta)}</i>"


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def render_article(a: dict, m: Meta) -> str:
    """The spec's mini-article post (Telegram HTML, ≤ 4096 chars): details are dropped from the end to fit."""
    details = list(a.get("details") or [])
    while True:
        more = [f"▸ {_e(d)}" for d in details]
        extra = []
        if a.get("try"):
            extra.append(f"🧪 <b>Попробовать:</b> {_e(a['try'])}")
        if a.get("caveats"):
            extra.append(f"⚠️ <b>Оговорки:</b> {_e(a['caveats'])}")
        hidden = "\n".join(more) + ("\n\n" if more and extra else "") + "\n".join(extra)
        blocks = [_head(a["title"], m), _e(a["lead"]), f"💡 {_e(a['why_it_matters'])}"]
        if hidden:
            blocks.append(f"<blockquote expandable>{hidden}</blockquote>")
        text = "\n\n".join(blocks)
        if links := _source_links(m.links):
            text += ("\n" if hidden else "\n\n") + links
        if len(text) <= MAX_MESSAGE_CHARS or not details:
            return text if len(text) <= MAX_MESSAGE_CHARS else text[:MAX_MESSAGE_CHARS]
        details.pop()


def render_card(title: str, summary: str | None, why: str | None, m: Meta) -> str:
    """Fallback when no mini-article could be written: the enrich fields."""
    blocks = [_head(_one_line(title, 200), m)]
    if summary:
        blocks.append(_e(summary))
    if why:
        blocks.append(f"💡 {_e(why)}")
    if links := _source_links(m.links):
        blocks.append(links)
    return "\n\n".join(blocks)


def render_overview(day: date, ov: dict | None, titles: list[str], later: list[str]) -> str:
    """Opening post of the day; without an overview (the call failed) only the table of contents.
    later: what the next waves bring («4–6 — в 13:00», «🌱 ранние сигналы — в 19:00»)."""
    blocks = [f"☕️ <b>Главное за {day.day} {MONTHS_GEN[day.month - 1]}</b>"]
    if ov:
        blocks.append(_e(ov["summary"]))
        blocks.append(f"🧭 <b>Куда движемся:</b> {_e(ov['direction'])}")
    toc = "\n".join(f"{KEYCAPS[i] if i < len(KEYCAPS) else f'{i + 1}.'} {_e(t)}" for i, t in enumerate(titles))
    blocks.append(f"<b>В выпуске</b>\n{toc}")
    if later:
        blocks[-1] += "\n\n<i>⏰ " + " · ".join(_e(x) for x in later) + "</i>"
    return "\n\n".join(blocks)


def render_weekly(start: date, end: date, w: dict) -> str:
    blocks = [f"🗓 <b>Неделя: куда движемся</b>\n<i>{week_label(start, end)}</i>"]
    if w["intro"]:
        blocks.append(_e(w["intro"]))
    for i, t in enumerate(w["trends"]):
        lines = [f"{KEYCAPS[i] if i < len(KEYCAPS) else f'{i + 1}.'} <b>{_e(t['name'])}</b>", _e(t["evidence"])]
        if t["direction"]:
            lines.append(f"🧭 {_e(t['direction'])}")
        if t["try"]:
            lines.append(f"🧪 {_e(t['try'])}")
        blocks.append("\n".join(lines))
    if w["watch"]:
        blocks.append(f"👀 <b>Присмотреться:</b> {_e(w['watch'])}")
    return "\n\n".join(blocks)[:MAX_MESSAGE_CHARS]
