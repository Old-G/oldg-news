"""Step 4 of the pipeline: glue items into stories (exact URL, else nearest centroid), heat = distinct sources."""

import logging
from dataclasses import dataclass

from config import Settings
from core.normalize import is_specific_url
from db import queries

log = logging.getLogger(__name__)


@dataclass
class Decision:
    story_id: int | None
    reason: str  # url | sim | new


def decide(
    url_match: tuple[int, float] | None,
    neighbors: list[tuple[int, float]],
    cluster_sim: float,
    url_min_sim: float,
) -> Decision:
    """url_match: (story id, similarity) of a story sharing the item's exact URL;
    neighbors: (story id, similarity) of the nearest stories, best first."""
    if url_match and url_match[1] >= url_min_sim:
        return Decision(url_match[0], "url")
    if neighbors and neighbors[0][1] >= cluster_sim:
        return Decision(neighbors[0][0], "sim")
    return Decision(None, "new")


def cluster_pending(conn, s: Settings) -> dict:
    """Attaches every embedded item without a story; safe to rerun."""
    stats = {"joined_url": 0, "joined_sim": 0, "new_stories": 0, "notes_stars": 0}
    # filtered kNN over HNSW: keep scanning until rows pass the 7-day filter
    conn.execute("set hnsw.iterative_scan = relaxed_order")
    for it in queries.items_to_cluster(conn):
        url_match = None
        if is_specific_url(it.url):
            url_match = queries.story_by_url(conn, it.url, it.embedding, s.story_match_days)
        neighbors = queries.nearest_stories(conn, it.embedding, s.story_match_days)
        d = decide(url_match, neighbors, s.cluster_sim, s.url_match_min_sim)
        if d.story_id:
            queries.attach_item(conn, it.id, d.story_id)
            story_id = d.story_id
            stats["joined_url" if d.reason == "url" else "joined_sim"] += 1
        else:
            story_id = queries.create_story(conn, it)
            stats["new_stories"] += 1
        if it.is_label_source:
            queries.add_feedback(conn, story_id, "star", "notes")
            stats["notes_stars"] += 1
        conn.commit()
    return stats
