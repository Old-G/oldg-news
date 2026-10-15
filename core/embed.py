"""Step 3 of the pipeline: embeddings for new items, in batches."""

import logging

import numpy as np
from openai import AsyncOpenAI

from config import Settings
from db import queries

log = logging.getLogger(__name__)


def embed_input(title: str | None, text: str | None, url: str | None, chars: int) -> str:
    return "\n".join(p for p in (title, (text or "")[:chars]) if p).strip() or (url or "")


async def embed_pending(conn, client: AsyncOpenAI, s: Settings) -> dict:
    """Embeds every normalized, non-ad item that has text or a URL; safe to rerun."""
    stats = {"embedded": 0, "tokens": 0, "usd": 0.0}
    while rows := queries.items_to_embed(conn, s.embed_batch):
        inputs = [embed_input(r.title, r.text, r.url, s.embed_text_chars) for r in rows]
        resp = await client.embeddings.create(model=s.embed_model, input=inputs, dimensions=s.embed_dimensions)
        vectors = {d.index: np.array(d.embedding, dtype=np.float32) for d in resp.data}
        queries.set_embeddings(conn, [(rows[i].id, vectors[i]) for i in range(len(rows))])
        conn.commit()
        stats["embedded"] += len(rows)
        stats["tokens"] += resp.usage.total_tokens
    stats["usd"] = round(stats["tokens"] / 1_000_000 * s.embed_usd_per_mtok, 4)
    return stats
