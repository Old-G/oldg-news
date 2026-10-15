"""Taste profile: centroids for the cheap prefilter + text for the score prompt."""

import difflib
import html
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import numpy as np
from openai import AsyncOpenAI

from config import Settings
from core import llm

MERGE_INSTRUCTION = "merge these into one profile with the same sections"


def kmeans(vectors: np.ndarray, k: int, iters: int = 100, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Spherical k-means (cosine) with k-means++ seeding. Returns unit centroids and cluster sizes."""
    x = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    k = min(k, len(x))
    rng = np.random.default_rng(seed)
    centers = [x[rng.integers(len(x))]]
    for _ in range(1, k):
        dist = np.clip(1 - np.max(x @ np.array(centers).T, axis=1), 0, None) ** 2
        centers.append(x[rng.choice(len(x), p=dist / dist.sum())] if dist.sum() > 0 else x[rng.integers(len(x))])
    c = np.array(centers)
    for _ in range(iters):
        labels = np.argmax(x @ c.T, axis=1)
        new = np.array([x[labels == j].mean(axis=0) if (labels == j).any() else c[j] for j in range(k)])
        new /= np.linalg.norm(new, axis=1, keepdims=True)
        if np.allclose(new, c):
            break
        c = new
    labels = np.argmax(x @ c.T, axis=1)
    return c, np.bincount(labels, minlength=k)


@dataclass
class NoteItem:
    published_at: datetime | None
    title: str | None
    text: str | None
    url: str | None
    forwarded: bool
    label: str | None = None  # source kind for the prompt when it is not a notes post («starred in digest»…)


# feedback (action, origin) → the item's source kind in the taste_profile prompt
SIGNAL_LABELS = {
    ("star", "notes"): None, ("star", "bot"): "starred in his digest", ("up", "bot"): "upvoted in his digest",
    ("missed", "bot"): "he found it elsewhere: the digest missed it", ("down", "bot"): "downvoted in his digest",
}


def format_item(it: NoteItem) -> str:
    """The prompt's item: date, title, source kind, 1–3 lines of text or summary, URL domain."""
    date = it.published_at.date().isoformat() if it.published_at else "?"
    kind = it.label or ("forwarded post" if it.forwarded else "saved link")
    domain = (urlsplit(it.url).hostname or "").removeprefix("www.") if it.url else "-"
    title = re.sub(r"\s+", " ", it.title or "").strip()
    lines = [
        re.sub(r"\s+", " ", ln).strip()[:200]
        for ln in re.sub(r"https?://\S+", "", it.text or "").splitlines()
    ]
    lines = [ln for ln in lines if ln and ln != title][:3]
    return "\n".join([f"- {date} | {title[:150]} | {kind} | {domain}", *(f"  {ln}" for ln in lines)])


async def write_profile(
    client: AsyncOpenAI, s: Settings, positive: list[str], negative: list[str], usage: llm.Usage
) -> str:
    """The taste_profile prompt on the given items; chunks of taste_chunk_n + one merge call if too long."""
    prompt = llm.load_prompt("taste_profile")
    model = s.taste_model or s.strong_model

    async def one(pos: list[str]) -> str:
        user = llm.render(
            prompt.user,
            n_pos=len(pos), positive_items="\n".join(pos),
            n_neg=len(negative), negative_items="\n".join(negative) or "(none yet)",
        )
        return await llm.complete(client, s, model, prompt.system, user, usage)

    if sum(len(p) for p in positive) <= s.taste_max_input_chars:
        return await one(positive)
    parts = [await one(positive[i : i + s.taste_chunk_n]) for i in range(0, len(positive), s.taste_chunk_n)]
    merge_input = f"{MERGE_INSTRUCTION}:\n\n" + "\n\n---\n\n".join(parts)
    return await llm.complete(client, s, model, prompt.system, merge_input, usage)


async def update_profile(client: AsyncOpenAI, s: Settings, current: str, version: int, since: str,
                         positive: list[str], negative: list[str], usage: llm.Usage) -> str:
    """The taste_update prompt: the current profile changed only where the new signals show it.
    No new signals → the current text as it is, no call."""
    if not positive and not negative:
        return current
    prompt = llm.load_prompt("taste_update")
    user = llm.render(prompt.user, version=version, profile_text=current, since=since,
                      n_pos=len(positive), positive_items="\n".join(positive) or "(none)",
                      n_neg=len(negative), negative_items="\n".join(negative) or "(none)")
    return await llm.complete(client, s, s.taste_model or s.strong_model, prompt.system, user, usage)


def neg_k(n: int, k: int) -> int:
    """Negative centroids: k of them once there are enough downvotes, none before (one stray 👎 is not a taste)."""
    return k if n >= k else 0


def profile_diff(old: str, new: str) -> list[str]:
    """Changed lines only: «+ …» added, «− …» removed (unified diff without headers and context)."""
    out = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line[1:].strip():
            out.append(("+ " if line[0] == "+" else "− ") + line[1:].strip())
    return out


def diff_message(old_version: int | None, new_version: int, built_from: dict, diff: list[str],
                 missed: dict[str, int], activated: bool, limit: int = 4096) -> str:
    """The weekly message to the owner's DM (Telegram HTML): what the profile was built from, what changed."""
    counts = built_from["signals"]
    head = [f"🧭 <b>Профиль вкуса v{new_version}</b>" + (f" (был v{old_version})" if old_version else ""),
            f"Позитив: {built_from['positive']} (заметки {counts.get('notes', 0)}, ⭐ {counts.get('star', 0)}, "
            f"👍 {counts.get('up', 0)}, missed {counts.get('missed', 0)}) · 👎 {built_from['negative']}",
            f"Центроиды: {built_from['k_pos']} pos, {built_from['k_neg']} neg · новых сигналов с прошлой версии: "
            f"+{built_from['new_positive']} / −{built_from['new_negative']}"]
    if missed:
        head.append("Missed за неделю: " + " · ".join(f"{k} {v}" for k, v in sorted(missed.items(), key=lambda x: -x[1])))
    tail = ("Активен с этого момента. Откат: <code>python -m jobs.bootstrap_taste --activate "
            f"{old_version}</code>" if activated and old_version else
            "Превью: версия не активна. Включить: <code>python -m jobs.bootstrap_taste --activate "
            f"{new_version}</code>" if not activated else "Активен.")
    body = "\n".join(html.escape(d) for d in diff) or "Текст не изменился."
    room = limit - len("\n".join(head)) - len(tail) - 80
    if len(body) > room:
        body = body[:room].rsplit("\n", 1)[0] + "\n…"
    return "\n".join(head) + f"\n\n<b>Что изменилось</b>\n<blockquote expandable>{body}</blockquote>\n{tail}"
