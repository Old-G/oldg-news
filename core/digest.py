"""Steps 8 and 10 of the pipeline: pick the day's stories, split them into waves, buttons and links.

Pure functions: no network, no database.
"""

from dataclasses import dataclass

MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


@dataclass
class Candidate:
    story_id: int
    prefilter_sim: float
    heat: int
    kinds: list[str]
    manual: bool = False
    notes: bool = False
    category: str | None = None
    llm_score: float | None = None
    final_score: float | None = None
    is_early: bool = False
    avg_weight: float = 1.0
    enriched: bool = False
    owner_actions: frozenset[str] = frozenset()


def interim_score(c: Candidate, heat_bonus: float) -> float:
    """Stories the LLM did not score (its batch failed): taste similarity plus a little per extra source."""
    return c.prefilter_sim + heat_bonus * (min(c.heat, 8) - 1)


def all_batches_failed(n_inputs: int, batch: int, failed: int) -> bool:
    """A stage that had work and lost every batch (usually no network to the LLM): a pick made now would rest
    on yesterday's scores, so the digest is not built and the next wave tries again."""
    return n_inputs > 0 and failed >= -(-n_inputs // batch)


def select(cands: list[Candidate], n: int, max_per_category: int, early_n: int,
           heat_bonus: float) -> tuple[list[Candidate], list[Candidate]]:
    """Spec step 8: (top, early). Manual /add stories go first and do not count against n; the rest by
    final_score with at most max_per_category per category; unscored stories only fill what is left.
    Early signals: up to early_n of the rest flagged is_early, by final_score."""
    manual = [c for c in cands if c.manual]
    scored = sorted((c for c in cands if not c.manual and c.final_score is not None),
                    key=lambda c: c.final_score, reverse=True)
    unscored = sorted((c for c in cands if not c.manual and c.final_score is None),
                      key=lambda c: interim_score(c, heat_bonus), reverse=True)
    top, per_cat = [], {}
    for c in scored + unscored:
        if len(top) == n:
            break
        cat = c.category or "other"
        if per_cat.get(cat, 0) < max_per_category:
            top.append(c)
            per_cat[cat] = per_cat.get(cat, 0) + 1
    chosen = {c.story_id for c in top}
    early = [c for c in scored if c.is_early and c.story_id not in chosen][:early_n]
    return manual + top, early


def wave_of(position: int, section: str, wave1_n: int) -> int:
    if section == "early":
        return 3
    return 1 if position <= wave1_n else 2


def later_lines(n_top: int, has_early: bool, waves: list[str], wave1_n: int) -> list[str]:
    """The overview's last line: what the next waves bring («4–6 — в 13:00», «🌱 ранние сигналы — в 19:00»)."""
    out = []
    if n_top > wave1_n and len(waves) >= 2:
        span = str(n_top) if n_top == wave1_n + 1 else f"{wave1_n + 1}–{n_top}"
        out.append(f"{span} — в {waves[1]}")
    if has_early and len(waves) >= 3:
        out.append(f"🌱 ранние сигналы — в {waves[2]}")
    return out


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


def story_links(card: dict, is_specific) -> list[str]:
    """A story's links from queries.story_card: the article first, then the Telegram post as the source."""
    items = card["items"]
    links = [card["canonical_url"]] + [i["url"] for i in items] + [u for i in items for u in (i["urls"] or [])]
    urls = [u for u in links if is_specific(u)]
    urls += [i["tg_link"] for i in items if i["tg_link"]]
    return list(dict.fromkeys(urls))
