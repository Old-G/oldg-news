"""All SQL of the pipeline. Callers own transactions: commit after a consistent unit of work."""

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from adapters import RawItem
from core.normalize import content_hash


def connect(url: str) -> psycopg.Connection:
    conn = psycopg.connect(url)
    register_vector(conn)
    return conn


@dataclass
class Source:
    id: int
    enabled: bool
    last_external_id: str | None


def upsert_source(
    conn: psycopg.Connection, kind: str, external_id: str, title: str | None, is_label_source: bool = False
) -> Source:
    row = conn.execute(
        """
        insert into sources (kind, external_id, title, is_label_source)
        values (%s, %s, %s, %s)
        on conflict (kind, external_id) do update
          set title = excluded.title,
              is_label_source = sources.is_label_source or excluded.is_label_source
        returning id, enabled, last_external_id
        """,
        (kind, external_id, title, is_label_source),
    ).fetchone()
    return Source(*row)


@dataclass
class FeedSource:
    id: int
    kind: str
    external_id: str
    title: str | None


def feed_sources(conn: psycopg.Connection) -> list[FeedSource]:
    rows = conn.execute(
        """
        select id, kind, external_id, title from sources
         where enabled and kind in ('rss', 'arxiv', 'hf_papers', 'hn')
         order by kind, id
        """
    ).fetchall()
    return [FeedSource(*r) for r in rows]


def existing_urls(conn: psycopg.Connection, kind: str, urls: list[str]) -> set[str]:
    """URLs already stored by any source of this kind (arXiv cross-lists repeat across categories)."""
    rows = conn.execute(
        "select i.url from items i join sources s on s.id = i.source_id where s.kind = %s and i.url = any(%s)",
        (kind, urls),
    ).fetchall()
    return {r[0] for r in rows}


def insert_manual_item(conn: psycopg.Connection, source_id: int, item: RawItem) -> tuple[int, bool]:
    """Returns (item id, created); an already known URL returns the stored item."""
    inserted = insert_items(conn, source_id, [item])
    row = conn.execute(
        "select id from items where source_id = %s and external_id = %s", (source_id, item.external_id)
    ).fetchone()
    return row[0], bool(inserted)


def insert_items(conn: psycopg.Connection, source_id: int, items: list[RawItem]) -> int:
    """Inserts new items, skips ones already stored; returns how many were new."""
    new = 0
    for it in items:
        cur = conn.execute(
            """
            insert into items (source_id, external_id, url, urls, title, text, published_at, content_hash, raw)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (source_id, external_id) do nothing
            """,
            (
                source_id,
                it.external_id,
                it.url,
                it.raw.get("urls", []),
                it.title,
                it.text,
                it.published_at,
                content_hash(it.url, it.text),
                Jsonb(it.raw),
            ),
        )
        new += cur.rowcount
    return new


def update_source_cursor(conn: psycopg.Connection, source_id: int, last_external_id: str | None) -> None:
    conn.execute(
        """
        update sources
           set last_external_id = coalesce(%s, last_external_id), last_fetched_at = now()
         where id = %s
        """,
        (last_external_id, source_id),
    )


def start_run(conn: psycopg.Connection, job: str) -> int:
    run_id = conn.execute("insert into runs (job) values (%s) returning id", (job,)).fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn: psycopg.Connection, run_id: int, stats: dict, error: str | None = None) -> None:
    conn.execute(
        "update runs set finished_at = now(), stats = %s, error = %s where id = %s",
        (Jsonb(stats), error, run_id),
    )
    conn.commit()


# --- normalize ---------------------------------------------------------------

@dataclass
class RawRow:
    id: int
    kind: str
    title: str | None
    text: str | None
    url: str | None
    urls: list[str]
    link: str | None  # t.me/<username>/<id> for Telegram items
    is_label_source: bool


def items_to_normalize(conn: psycopg.Connection, limit: int) -> list[RawRow]:
    rows = conn.execute(
        """
        select i.id, s.kind, i.title, i.text, i.url, i.urls, i.raw->>'link', s.is_label_source
          from items i join sources s on s.id = i.source_id
         where i.lang is null
         order by i.id limit %s
        """,
        (limit,),
    ).fetchall()
    return [RawRow(*r) for r in rows]


def set_normalized(conn: psycopg.Connection, rows: list[tuple]) -> None:
    """rows: (text, url, content_hash, lang, is_ad, id)"""
    with conn.cursor() as cur:
        cur.executemany(
            "update items set text = %s, url = %s, content_hash = %s, lang = %s, is_ad = %s where id = %s", rows
        )


# --- embed -------------------------------------------------------------------

@dataclass
class EmbedRow:
    id: int
    title: str | None
    text: str | None
    url: str | None


def items_to_embed(conn: psycopg.Connection, limit: int) -> list[EmbedRow]:
    rows = conn.execute(
        """
        select id, title, text, url from items
         where embedding is null and lang is not null and not is_ad
           and (coalesce(text, '') <> '' or coalesce(title, '') <> '' or url is not null)
           and source_id in (select id from sources where enabled)
         order by id limit %s
        """,
        (limit,),
    ).fetchall()
    return [EmbedRow(*r) for r in rows]


def set_embeddings(conn: psycopg.Connection, rows: list[tuple[int, np.ndarray]]) -> None:
    with conn.cursor() as cur:
        cur.executemany("update items set embedding = %s where id = %s", [(v, i) for i, v in rows])


# --- cluster -----------------------------------------------------------------

@dataclass
class ClusterRow:
    id: int
    source_id: int
    url: str | None
    title: str | None
    published_at: datetime | None
    embedding: np.ndarray
    is_label_source: bool


def items_to_cluster(conn: psycopg.Connection) -> list[ClusterRow]:
    rows = conn.execute(
        """
        select i.id, i.source_id, i.url, i.title, i.published_at, i.embedding, s.is_label_source
          from items i join sources s on s.id = i.source_id
         where i.story_id is null and i.embedding is not null and s.enabled
         order by i.published_at nulls last, i.id
        """
    ).fetchall()
    return [ClusterRow(*r) for r in rows]


_SAME_SOURCE = "exists (select 1 from items m where m.story_id = st.id and m.source_id = %s)"


def story_by_url(
    conn: psycopg.Connection, url: str, embedding: np.ndarray, source_id: int, days: int
) -> tuple[int, float, bool] | None:
    row = conn.execute(
        f"""
        select st.id, 1 - (st.centroid <=> %s) as sim, {_SAME_SOURCE}
          from items i join stories st on st.id = i.story_id
         where i.url = %s and st.last_seen_at > now() - make_interval(days => %s)
         order by sim desc limit 1
        """,
        (embedding, source_id, url, days),
    ).fetchone()
    return (row[0], row[1], row[2]) if row else None


def nearest_stories(
    conn: psycopg.Connection, embedding: np.ndarray, source_id: int, days: int
) -> list[tuple[int, float, bool]]:
    """The spec's story search: three nearest centroids among stories seen within `days`."""
    return [
        (r[0], r[1], r[2])
        for r in conn.execute(
            f"""
            select st.id, st.sim, {_SAME_SOURCE}
              from (select id, 1 - (centroid <=> %s) as sim from stories
                     where last_seen_at > now() - make_interval(days => %s)
                     order by centroid <=> %s limit 3) st
             order by st.sim desc
            """,
            (source_id, embedding, days, embedding),
        ).fetchall()
    ]


def create_story(conn: psycopg.Connection, it: ClusterRow) -> int:
    story_id = conn.execute(
        """
        insert into stories (canonical_url, title, first_seen_at, last_seen_at, centroid, heat)
        values (%s, %s, coalesce(least(%s, now()), now()), now(), %s, 1)
        returning id
        """,
        (it.url, it.title, it.published_at, it.embedding),
    ).fetchone()[0]
    conn.execute("update items set story_id = %s where id = %s", (story_id, it.id))
    return story_id


def attach_item(conn: psycopg.Connection, item_id: int, story_id: int) -> None:
    """centroid = mean of member embeddings, heat = distinct sources, last_seen_at = now()."""
    conn.execute("update items set story_id = %s where id = %s", (story_id, item_id))
    conn.execute(
        """
        update stories s
           set centroid = m.centroid, heat = m.heat, last_seen_at = now()
          from (select avg(embedding) as centroid, count(distinct source_id) as heat
                  from items where story_id = %s) m
         where s.id = %s
        """,
        (story_id, story_id),
    )


def add_feedback(
    conn: psycopg.Connection, story_id: int, action: str, origin: str, digest_id: int | None = None
) -> None:
    conn.execute(
        "insert into feedback (story_id, digest_id, action, origin) values (%s, %s, %s, %s)",
        (story_id, digest_id, action, origin),
    )


# --- taste -------------------------------------------------------------------

def bootstrap_note_stories(conn: psycopg.Connection, source_id: int) -> int:
    """One starred story per embedded notes item that has none, dated by the post itself.

    Old notes must not look fresh: no now() here, and status 'starred' keeps them out of digests.
    """
    rows = conn.execute(
        "select id, url, title, published_at, embedding from items"
        " where source_id = %s and story_id is null and embedding is not null order by published_at",
        (source_id,),
    ).fetchall()
    for item_id, url, title, published_at, embedding in rows:
        story_id = conn.execute(
            """
            insert into stories (canonical_url, title, first_seen_at, last_seen_at, centroid, heat, status)
            values (%s, %s, coalesce(%s, now()), coalesce(%s, now()), %s, 1, 'starred')
            returning id
            """,
            (url, title, published_at, published_at, embedding),
        ).fetchone()[0]
        conn.execute("update items set story_id = %s where id = %s", (story_id, item_id))
        add_feedback(conn, story_id, "star", "notes")
    return len(rows)


def note_items(conn: psycopg.Connection, source_id: int, limit: int | None = None) -> list[tuple]:
    """(published_at, title, text, url, forwarded, embedding), newest first."""
    return conn.execute(
        """
        select published_at, title, text, url, (raw->>'fwd_from_channel_id') is not null, embedding
          from items where source_id = %s and embedding is not null
         order by published_at desc nulls last limit %s
        """,
        (source_id, limit),
    ).fetchall()


def create_taste_profile(
    conn: psycopg.Connection, text: str, built_from: dict, centroids: list[tuple[np.ndarray, int]]
) -> tuple[int, int]:
    """A new inactive version with its positive centroids; returns (id, version)."""
    profile_id, version = conn.execute(
        """
        insert into taste_profiles (version, profile_text, built_from)
        values ((select coalesce(max(version), 0) + 1 from taste_profiles), %s, %s)
        returning id, version
        """,
        (text, Jsonb(built_from)),
    ).fetchone()
    with conn.cursor() as cur:
        cur.executemany(
            "insert into taste_centroids (profile_id, polarity, centroid, n_items) values (%s, 'pos', %s, %s)",
            [(profile_id, c, int(n)) for c, n in centroids],
        )
    return profile_id, version


def activate_taste_profile(conn: psycopg.Connection, version: int, text: str | None = None) -> None:
    """Exactly one active version; optional text replaces the generated one (the manual edit)."""
    if text is not None:
        conn.execute("update taste_profiles set profile_text = %s where version = %s", (text, version))
    conn.execute("update taste_profiles set is_active = (version = %s)", (version,))


# --- digest ------------------------------------------------------------------

def active_profile_id(conn: psycopg.Connection) -> int | None:
    row = conn.execute("select id from taste_profiles where is_active").fetchone()
    return row[0] if row else None


def digest_candidates(conn: psycopg.Connection, profile_id: int, window_h: int) -> list[tuple]:
    """(story_id, prefilter_sim, heat, source kinds, has manual item) for new stories seen within the window.

    prefilter_sim = max cosine to the active profile's positive centroids (spec: prefilter query).
    """
    return conn.execute(
        """
        select s.id,
               (select max(1 - (s.centroid <=> c.centroid)) from taste_centroids c
                 where c.profile_id = %s and c.polarity = 'pos') as sim,
               s.heat,
               array_agg(distinct src.kind),
               bool_or(src.kind = 'manual')
          from stories s join items i on i.story_id = s.id join sources src on src.id = i.source_id
         where s.status = 'new' and s.last_seen_at > now() - make_interval(hours => %s)
         group by s.id
        """,
        (profile_id, window_h),
    ).fetchall()


def set_prefilter_sims(conn: psycopg.Connection, rows: list[tuple[float, int]]) -> None:
    with conn.cursor() as cur:
        cur.executemany("update stories set prefilter_sim = %s where id = %s", rows)


def dismiss_stale(conn: psycopg.Connection, hours: int) -> int:
    return conn.execute(
        "update stories set status = 'dismissed' where status = 'new' and last_seen_at < now() - make_interval(hours => %s)",
        (hours,),
    ).rowcount


def digest_for(conn: psycopg.Connection, day) -> tuple[int, int | None] | None:
    row = conn.execute("select id, header_msg_id from digests where for_date = %s", (day,)).fetchone()
    return (row[0], row[1]) if row else None


def create_digest(conn: psycopg.Connection, day, entries: list[tuple[int, int, str]]) -> int:
    """entries: (story_id, position, section). Chosen stories become 'digested'."""
    digest_id = conn.execute("insert into digests (for_date) values (%s) returning id", (day,)).fetchone()[0]
    add_digest_entries(conn, digest_id, entries)
    return digest_id


def add_digest_entries(conn: psycopg.Connection, digest_id: int, entries: list[tuple[int, int, str]]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "insert into digest_entries (digest_id, story_id, position, section) values (%s, %s, %s, %s)"
            " on conflict do nothing",
            [(digest_id, sid, pos, sec) for sid, pos, sec in entries],
        )
        cur.executemany("update stories set status = 'digested' where id = %s and status = 'new'",
                        [(sid,) for sid, _, _ in entries])


def digest_entries(conn: psycopg.Connection, digest_id: int) -> list[tuple]:
    """(story_id, position, section, tg_msg_id) in order."""
    return conn.execute(
        "select story_id, position, section, tg_msg_id from digest_entries where digest_id = %s"
        " order by section desc, position",  # 'top' before 'early'
        (digest_id,),
    ).fetchall()


def set_entry_message(conn: psycopg.Connection, digest_id: int, story_id: int, msg_id: int) -> None:
    conn.execute("update digest_entries set tg_msg_id = %s where digest_id = %s and story_id = %s",
                 (msg_id, digest_id, story_id))


def set_header_message(conn: psycopg.Connection, digest_id: int, msg_id: int) -> None:
    conn.execute("update digests set header_msg_id = %s, sent_at = now() where id = %s", (msg_id, digest_id))


def story_card(conn: psycopg.Connection, story_id: int) -> dict:
    """What a digest post needs: story fields plus the items' links and the longest text."""
    st = conn.execute(
        "select title, heat, canonical_url, summary, why_it_matters, category, tags from stories where id = %s",
        (story_id,),
    ).fetchone()
    items = conn.execute(
        """
        select i.title, i.text, i.url, i.urls, i.raw->>'link', src.kind
          from items i join sources src on src.id = i.source_id
         where i.story_id = %s order by length(coalesce(i.text, '')) desc
        """,
        (story_id,),
    ).fetchall()
    return {
        "title": st[0], "heat": st[1], "canonical_url": st[2], "summary": st[3], "why_it_matters": st[4],
        "category": st[5], "tags": st[6] or [],
        "items": [dict(zip(("title", "text", "url", "urls", "tg_link", "kind"), r)) for r in items],
    }


# --- feedback ----------------------------------------------------------------

def owner_actions(conn: psycopg.Connection, story_id: int) -> set[str]:
    rows = conn.execute(
        "select distinct action from feedback where story_id = %s and origin = 'bot'", (story_id,)
    ).fetchall()
    return {r[0] for r in rows}


def latest_digest_of(conn: psycopg.Connection, story_id: int) -> int | None:
    row = conn.execute(
        "select d.id from digest_entries e join digests d on d.id = e.digest_id where e.story_id = %s"
        " order by d.for_date desc limit 1",
        (story_id,),
    ).fetchone()
    return row[0] if row else None


def set_story_status(conn: psycopg.Connection, story_id: int, status: str) -> None:
    conn.execute("update stories set status = %s where id = %s", (status, story_id))


def feedback_counts(conn: psycopg.Connection, since, until) -> dict[str, int]:
    rows = conn.execute(
        "select action, count(*) from feedback where origin = 'bot' and created_at >= %s and created_at < %s"
        " group by action",
        (since, until),
    ).fetchall()
    return dict(rows)


def week_stats(conn: psycopg.Connection) -> dict:
    """/stats: the last 7 days."""
    row = conn.execute(
        """
        select
          (select count(*) from stories s where s.created_at > now() - interval '7 days'
              and not exists (select 1 from feedback f where f.story_id = s.id and f.origin = 'notes')),
          (select count(*) from digest_entries e join digests d on d.id = e.digest_id
            where d.for_date > current_date - 7),
          (select count(*) filter (where action = 'up') from feedback where origin = 'bot' and created_at > now() - interval '7 days'),
          (select count(*) filter (where action = 'down') from feedback where origin = 'bot' and created_at > now() - interval '7 days'),
          (select count(*) filter (where action = 'star') from feedback where origin = 'bot' and created_at > now() - interval '7 days'),
          (select count(*) from feedback where action = 'missed' and created_at > now() - interval '7 days'),
          (select count(*) from feedback where origin = 'audience' and created_at > now() - interval '7 days'),
          (select coalesce(sum((stats->>'usd')::numeric), 0) from runs where started_at > now() - interval '7 days')
        """
    ).fetchone()
    keys = ("stories", "in_digest", "up", "down", "star", "missed", "audience", "usd")
    return dict(zip(keys, row))
