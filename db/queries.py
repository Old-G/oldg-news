"""All SQL of the pipeline. Callers own transactions: commit after a consistent unit of work."""

from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from adapters import RawItem
from core.normalize import content_hash


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url)


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
