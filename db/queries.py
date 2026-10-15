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
    cursor: str | None = None  # last_external_id; page_watch / hf_trending / github_search keep what they saw here
    fetched_at: datetime | None = None


def feed_sources(conn: psycopg.Connection) -> list[FeedSource]:
    rows = conn.execute(
        """
        select id, kind, external_id, title, last_external_id, last_fetched_at from sources
         where enabled and kind in ('rss', 'arxiv', 'hf_papers', 'hn', 'page_watch', 'hf_trending', 'github_search')
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
    conn: psycopg.Connection, text: str, built_from: dict, centroids: list[tuple[np.ndarray, int]],
    neg_centroids: list[tuple[np.ndarray, int]] = (),
) -> tuple[int, int]:
    """A new inactive version with its centroids; returns (id, version)."""
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
        cur.executemany(
            "insert into taste_centroids (profile_id, polarity, centroid, n_items) values (%s, 'neg', %s, %s)",
            [(profile_id, c, int(n)) for c, n in neg_centroids],
        )
    return profile_id, version


def activate_taste_profile(conn: psycopg.Connection, version: int, text: str | None = None) -> None:
    """Exactly one active version; optional text replaces the generated one (the manual edit)."""
    if text is not None:
        conn.execute("update taste_profiles set profile_text = %s where version = %s", (text, version))
    conn.execute("update taste_profiles set is_active = (version = %s)", (version,))


# --- digest ------------------------------------------------------------------

def active_profile(conn: psycopg.Connection) -> tuple[int, str] | None:
    """(id, profile_text) of the active taste profile."""
    row = conn.execute("select id, profile_text from taste_profiles where is_active").fetchone()
    return (row[0], row[1]) if row else None


def ranking_candidates(conn: psycopg.Connection, profile_id: int, window_h: int) -> list[dict]:
    """New stories seen within the window with what prefilter, score and select need.

    prefilter_sim = max cosine to the active profile's positive centroids minus half the max cosine to its
    negative ones (spec: weekly taste; no negative centroids yet → 0);
    kinds: an rss feed on github.com counts as 'github'; avg_weight over the story's distinct sources.
    """
    rows = conn.execute(
        """
        select s.id,
               (select max(1 - (s.centroid <=> c.centroid)) from taste_centroids c
                 where c.profile_id = %s and c.polarity = 'pos')
               - 0.5 * coalesce((select max(1 - (s.centroid <=> c.centroid)) from taste_centroids c
                                  where c.profile_id = %s and c.polarity = 'neg'), 0),
               s.heat,
               array_agg(distinct case when src.kind = 'rss' and src.external_id like '%%github.com/%%'
                                       then 'github' else src.kind end),
               bool_or(src.kind = 'manual'),
               bool_or(src.is_label_source),
               (select avg(w.weight) from sources w
                 where w.id in (select source_id from items where story_id = s.id)),
               s.enriched_at is not null, s.category, s.llm_score,
               array(select distinct f.action from feedback f where f.story_id = s.id and f.origin = 'bot')
          from stories s join items i on i.story_id = s.id join sources src on src.id = i.source_id
         where s.status = 'new' and s.last_seen_at > now() - make_interval(hours => %s)
         group by s.id
        """,
        (profile_id, profile_id, window_h),
    ).fetchall()
    keys = ("story_id", "sim", "heat", "kinds", "manual", "notes", "avg_weight", "enriched", "category",
            "llm_score", "owner_actions")
    return [dict(zip(keys, r)) for r in rows]


def enrich_inputs(conn: psycopg.Connection, story_ids: list[int]) -> list[tuple]:
    """(id, title, canonical_url, heat, longest item text, all item urls, source kinds)."""
    return conn.execute(
        """
        select s.id, s.title, s.canonical_url, s.heat,
               (select i.text from items i where i.story_id = s.id and not i.is_ad
                 order by length(coalesce(i.text, '')) desc limit 1),
               array(select u from items i, unnest(array_prepend(i.url, i.urls)) u
                      where i.story_id = s.id and u is not null),
               array(select distinct src.kind from items i join sources src on src.id = i.source_id
                      where i.story_id = s.id)
          from stories s where s.id = any(%s) order by s.id
        """,
        (story_ids,),
    ).fetchall()


def set_enriched(conn: psycopg.Connection, rows: list) -> None:
    """rows: core.enrich.Enriched; an ad is dismissed at once."""
    with conn.cursor() as cur:
        cur.executemany(
            """
            update stories set title = %s, summary = %s, why_it_matters = %s, category = %s, tags = %s,
                   enriched_at = now(), status = case when %s then 'dismissed' else status end
             where id = %s
            """,
            [(r.title_clean, r.summary, r.why_it_matters, r.category, r.tags, r.is_ad, r.story_id) for r in rows],
        )


def score_inputs(conn: psycopg.Connection, story_ids: list[int]) -> list[dict]:
    rows = conn.execute(
        """
        select id, title, summary, why_it_matters, category, tags, heat,
               round(extract(epoch from now() - first_seen_at) / 3600)::int
          from stories where id = any(%s) and enriched_at is not null order by id
        """,
        (story_ids,),
    ).fetchall()
    keys = ("story_id", "title_clean", "summary", "why_it_matters", "category", "tags", "n_sources",
            "first_seen_hours_ago")
    return [dict(zip(keys, r)) for r in rows]


def set_scores(conn: psycopg.Connection, scores: dict[int, tuple[float, str]]) -> None:
    with conn.cursor() as cur:
        cur.executemany("update stories set llm_score = %s, llm_reason = %s, scored_at = now() where id = %s",
                        [(sc, reason, sid) for sid, (sc, reason) in scores.items()])


def set_final_scores(conn: psycopg.Connection, rows: list[tuple[float, bool, int]]) -> None:
    """rows: (final_score, is_early_signal, story_id)."""
    with conn.cursor() as cur:
        cur.executemany("update stories set final_score = %s, is_early_signal = %s where id = %s", rows)


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
        """
        select title, heat, canonical_url, summary, why_it_matters, category, tags, article,
               article_at is null or article_at < last_seen_at
          from stories where id = %s
        """,
        (story_id,),
    ).fetchone()
    items = conn.execute(
        """
        select i.title, i.text, i.url, i.urls, i.raw->>'link', src.kind
          from items i join sources src on src.id = i.source_id
         where i.story_id = %s and not i.is_ad order by length(coalesce(i.text, '')) desc
        """,
        (story_id,),
    ).fetchall()
    return {
        "title": st[0], "heat": st[1], "canonical_url": st[2], "summary": st[3], "why_it_matters": st[4],
        "category": st[5], "tags": st[6] or [], "article": st[7], "article_stale": st[8],
        "items": [dict(zip(("title", "text", "url", "urls", "tg_link", "kind"), r)) for r in items],
    }


def set_article(conn: psycopg.Connection, story_id: int, article: dict, model: str) -> None:
    conn.execute("update stories set article = %s, article_model = %s, article_at = now() where id = %s",
                 (Jsonb(article), model, story_id))


def digest_overview(conn: psycopg.Connection, digest_id: int) -> dict | None:
    return conn.execute("select overview from digests where id = %s", (digest_id,)).fetchone()[0]


def set_digest_overview(conn: psycopg.Connection, digest_id: int, overview: dict) -> None:
    conn.execute("update digests set overview = %s where id = %s", (Jsonb(overview), digest_id))


def week_articles(conn: psycopg.Connection, start, end) -> list[tuple[dict, list[str]]]:
    """(article, the owner's actions) for stories published in the channel in digests of [start, end]."""
    return conn.execute(
        """
        select s.article,
               array(select distinct f.action from feedback f where f.story_id = s.id and f.origin = 'bot')
          from digest_entries e join digests d on d.id = e.digest_id join stories s on s.id = e.story_id
         where d.for_date between %s and %s and e.tg_msg_id is not null and s.article is not null
         order by d.for_date, e.section desc, e.position
        """,
        (start, end),
    ).fetchall()


def weekly_post(conn: psycopg.Connection, week_start) -> tuple[dict, int | None] | None:
    row = conn.execute("select body, tg_msg_id from weekly_posts where week_start = %s", (week_start,)).fetchone()
    return (row[0], row[1]) if row else None


def save_weekly_post(conn: psycopg.Connection, week_start, body: dict, model: str) -> None:
    conn.execute(
        "insert into weekly_posts (week_start, body, model) values (%s, %s, %s) on conflict (week_start) do nothing",
        (week_start, Jsonb(body), model),
    )


def set_weekly_message(conn: psycopg.Connection, week_start, msg_id: int) -> None:
    conn.execute("update weekly_posts set tg_msg_id = %s where week_start = %s", (msg_id, week_start))


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


def story_of_message(conn: psycopg.Connection, msg_id: int) -> int | None:
    """The story a digest post in the channel carries (digest_entries.tg_msg_id)."""
    row = conn.execute("select story_id from digest_entries where tg_msg_id = %s", (msg_id,)).fetchone()
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


# --- drafts ------------------------------------------------------------------

def stars_without_drafts(conn: psycopg.Connection, hours: int) -> list[int]:
    """Stories the owner starred in the bot within `hours` that have no drafts yet (notes stars never count)."""
    rows = conn.execute(
        """
        select f.story_id from feedback f
         where f.action = 'star' and f.origin = 'bot' and f.created_at > now() - make_interval(hours => %s)
           and not exists (select 1 from drafts d where d.story_id = f.story_id)
         group by f.story_id order by min(f.created_at)
        """,
        (hours,),
    ).fetchall()
    return [r[0] for r in rows]


def add_drafts(conn: psycopg.Connection, story_id: int, rows: list[tuple[str, str, str]], model: str) -> None:
    """rows: (lang, channel, text)."""
    with conn.cursor() as cur:
        cur.executemany(
            "insert into drafts (story_id, lang, channel, text, model) values (%s, %s, %s, %s, %s)",
            [(story_id, lang, channel, text, model) for lang, channel, text in rows],
        )


# --- weekly taste and /missed --------------------------------------------------

POSITIVE = "(f.action in ('star', 'up', 'missed') and f.origin in ('bot', 'notes'))"
NEGATIVE = "(f.action = 'down' and f.origin = 'bot')"
# a notes post is dated by the post itself (its story's first_seen_at); a bot press by the press
_SIGNAL_AT = "case when f.origin = 'notes' then s.first_seen_at else f.created_at end"


def taste_vectors(conn: psycopg.Connection, positive: bool) -> list[np.ndarray]:
    """Centroids of every story the owner marked: positive (⭐ / 👍 / missed / notes) or negative (👎)."""
    rows = conn.execute(
        f"""
        select distinct on (s.id) s.centroid from feedback f join stories s on s.id = f.story_id
         where {POSITIVE if positive else NEGATIVE} and s.centroid is not null
        """
    ).fetchall()
    return [r[0].to_numpy() for r in rows]


def taste_signals(conn: psycopg.Connection, positive: bool, since, limit: int) -> list[tuple]:
    """(signal_at, title, text, url, forwarded, action, origin) of marked stories after `since`, newest first.
    Text: the enrich summary, else the longest item text."""
    return conn.execute(
        f"""
        select * from (
          select distinct on (s.id) {_SIGNAL_AT} as at, s.title,
                 coalesce(s.summary, (select i.text from items i where i.story_id = s.id
                                       order by length(coalesce(i.text, '')) desc limit 1)),
                 s.canonical_url,
                 exists (select 1 from items i where i.story_id = s.id and i.raw ? 'fwd_from_channel_id'),
                 f.action, f.origin
            from feedback f join stories s on s.id = f.story_id
           where {POSITIVE if positive else NEGATIVE} and {_SIGNAL_AT} > %s
           order by s.id, f.origin = 'bot' desc, f.created_at desc
        ) t order by at desc limit %s
        """,
        (since, limit),
    ).fetchall()


def signal_counts(conn: psycopg.Connection) -> dict[str, int]:
    """Stories per kind of positive signal: notes, star, up, missed."""
    rows = conn.execute(
        f"""
        select case when f.origin = 'notes' then 'notes' else f.action end, count(distinct f.story_id)
          from feedback f where {POSITIVE} group by 1
        """
    ).fetchall()
    return dict(rows)


def active_profile_row(conn: psycopg.Connection) -> tuple[int, int, str, dict, datetime] | None:
    """(id, version, profile_text, built_from, created_at) of the active profile."""
    return conn.execute(
        "select id, version, profile_text, built_from, created_at from taste_profiles where is_active"
    ).fetchone()


def missed_reasons(conn: psycopg.Connection, days: int) -> dict[str, int]:
    rows = conn.execute(
        "select stats->>'reason', count(*) from runs where job = 'missed' and started_at > now() - make_interval(days => %s)"
        " group by 1",
        (days,),
    ).fetchall()
    return {r: n for r, n in rows if r}


def find_story_by_url(conn: psycopg.Connection, url: str) -> int | None:
    """The newest story holding this URL as its main link, an item's link or one of an item's links."""
    row = conn.execute(
        """
        select s.id from stories s
         where s.canonical_url = %s
            or exists (select 1 from items i where i.story_id = s.id and (i.url = %s or %s = any(i.urls)))
         order by s.last_seen_at desc limit 1
        """,
        (url, url, url),
    ).fetchone()
    return row[0] if row else None


def missed_facts(conn: psycopg.Connection, story_id: int, window_h: int) -> dict:
    """What core.missed.cut_reason needs: the story's state, its digest, the digests built while it was
    a candidate (first seen … last seen + window) and the lowest final_score they picked."""
    row = conn.execute(
        """
        select s.status, s.enriched_at is not null, s.prefilter_sim, s.llm_score, s.final_score,
               (select min(d.for_date) from digest_entries e join digests d on d.id = e.digest_id
                 where e.story_id = s.id),
               (select count(*) from digests d where d.created_at between s.first_seen_at
                   and s.last_seen_at + make_interval(hours => %s) and exists (
                     select 1 from digest_entries e where e.digest_id = d.id)),
               (select min(x.final_score) from digests d join digest_entries e on e.digest_id = d.id
                  join stories x on x.id = e.story_id
                 where e.section = 'top' and d.created_at between s.first_seen_at
                   and s.last_seen_at + make_interval(hours => %s)),
               exists (select 1 from feedback f where f.story_id = s.id and f.action = 'down' and f.origin = 'bot'),
               s.first_seen_at, s.title
          from stories s where s.id = %s
        """,
        (window_h, window_h, story_id),
    ).fetchone()
    keys = ("status", "enriched", "prefilter_sim", "llm_score", "final_score", "digest_date", "digests_since",
            "cutoff", "owner_down", "first_seen_at", "title")
    return dict(zip(keys, row))


def item_with_url(conn: psycopg.Connection, url: str) -> bool:
    """A raw item has the URL even if no story holds it yet (fetched, not clustered)."""
    return conn.execute("select exists (select 1 from items where url = %s or %s = any(urls))", (url, url)).fetchone()[0]


def create_missed_story(conn: psycopg.Connection, url: str) -> int:
    """A record for a URL no source had: dismissed at once, so it never enters a digest; carries the missed feedback."""
    return conn.execute(
        "insert into stories (canonical_url, title, first_seen_at, last_seen_at, status)"
        " values (%s, %s, now(), now(), 'dismissed') returning id",
        (url, url),
    ).fetchone()[0]
