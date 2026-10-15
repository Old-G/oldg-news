"""Step 8 of the pipeline: pick the day's stories, split them into waves, render posts and buttons.

Pure functions: no network, no database.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import date

MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
PAPER_KINDS = {"arxiv", "hf_papers"}
MAX_POST_CHARS = 600


@dataclass
class Candidate:
    story_id: int
    prefilter_sim: float
    heat: int
    kinds: list[str]
    manual: bool = False
    llm_score: float | None = None
    final_score: float | None = None


def interim_score(c: Candidate, heat_bonus: float) -> float:
    """Until enrich/score exist: taste similarity plus a little for every extra independent source."""
    return c.prefilter_sim + heat_bonus * (min(c.heat, 8) - 1)


def select(candidates: list[Candidate], n: int, min_sim: float, heat_bonus: float) -> list[Candidate]:
    """Top n by interim score. A paper nobody else picked up and below min_sim is cut here (spec: prefilter).
    Manual /add stories go first and do not count against n."""
    manual = [c for c in candidates if c.manual]
    rest = [
        c for c in candidates
        if not c.manual and not (set(c.kinds) <= PAPER_KINDS and c.heat == 1 and c.prefilter_sim < min_sim)
    ]
    rest.sort(key=lambda c: interim_score(c, heat_bonus), reverse=True)
    return manual + rest[:n]


def wave_of(position: int, section: str, wave1_n: int) -> int:
    if section == "early":
        return 3
    return 1 if position <= wave1_n else 2


def header(day: date, n_top: int, n_early: int) -> str:
    text = f"Radar · {day.day} {MONTHS[day.month - 1]} · {n_top} {_plural(n_top, 'история', 'истории', 'историй')}"
    if n_early:
        text += f" + {n_early} {_plural(n_early, 'сигнал', 'сигнала', 'сигналов')}"
    return text


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


@dataclass
class StoryView:
    position: int
    title: str
    heat: int
    urls: list[str]
    snippet: str | None = None  # best item text until enrich gives a summary
    summary: str | None = None
    why_it_matters: str | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)


def render_story(v: StoryView) -> str:
    """The spec's story message (Telegram HTML, ≤ 600 chars)."""
    meta = [str(v.position)]
    if v.heat >= 2:
        meta.append(f"🔥 heat {v.heat}")
    if v.category:
        meta.append(v.category)
    if v.tags:
        meta.append(", ".join(v.tags))
    links = "\n".join(html.escape(u) for u in v.urls[:2])
    head = f"{' · '.join(meta)}\n<b>{html.escape(_one_line(v.title, 200))}</b>"
    if v.summary:
        body = html.escape(v.summary)
        if v.why_it_matters:
            body += f"\n<i>Почему важно:</i> {html.escape(v.why_it_matters)}"
    else:
        body = html.escape(v.snippet or "")
    room = MAX_POST_CHARS - len(head) - len(links) - 2
    if len(body) > room:
        body = html.escape(_cut(html.unescape(body), max(room - 20, 0)))
    return "\n".join(p for p in (head, body, links) if p)


def snippet(text: str | None, title: str | None, chars: int = 280) -> str | None:
    """A readable piece of the best item: no links, no repeated title."""
    lines = [ln.strip() for ln in re.sub(r"https?://\S+", "", text or "").splitlines()]
    lines = [ln for ln in lines if ln and ln != (title or "").strip()]
    return _cut(" ".join(lines), chars) or None


def _one_line(s: str, n: int) -> str:
    return _cut(" ".join(s.split()), n)


def _cut(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:—-") + "…"


# --- buttons -----------------------------------------------------------------

def buttons(owner_actions: set[str]) -> list[tuple[str, str]]:
    """(label, action) for the owner's state. ⭐ after 👍 is allowed, nothing after 👎 or ⭐."""
    if "star" in owner_actions:
        return [("✓ 👍 ⭐" if "up" in owner_actions else "✓ ⭐", "noop")]
    if "down" in owner_actions:
        return [("✓ 👎", "noop")]
    if "up" in owner_actions:
        return [("✓ 👍", "noop"), ("⭐ пост", "star")]
    return [("👍", "up"), ("👎", "down"), ("⭐ пост", "star")]


def owner_may(action: str, owner_actions: set[str]) -> bool:
    return action in {a for _, a in buttons(owner_actions)} - {"noop"}


def view_from_card(card: dict, position: int, is_specific) -> StoryView:
    """StoryView from queries.story_card: links first (article, then the Telegram post as the source)."""
    items = card["items"]
    links = [card["canonical_url"]] + [i["url"] for i in items] + [u for i in items for u in (i["urls"] or [])]
    urls = [u for u in links if is_specific(u)]
    urls += [i["tg_link"] for i in items if i["tg_link"]]
    best = items[0] if items else {}
    title = card["title"] or best.get("title") or (urls[0] if urls else "")
    return StoryView(
        position=position, title=title, heat=card["heat"], urls=list(dict.fromkeys(urls)),
        snippet=snippet(best.get("text"), title), summary=card["summary"],
        why_it_matters=card["why_it_matters"], category=card["category"], tags=card["tags"],
    )
