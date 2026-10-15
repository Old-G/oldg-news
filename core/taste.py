"""Taste profile: centroids for the cheap prefilter + text for the score prompt."""

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


def format_item(it: NoteItem) -> str:
    """The prompt's item: date, title, source kind, 1–3 lines of text or summary, URL domain."""
    date = it.published_at.date().isoformat() if it.published_at else "?"
    kind = "forwarded post" if it.forwarded else "saved link"
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
