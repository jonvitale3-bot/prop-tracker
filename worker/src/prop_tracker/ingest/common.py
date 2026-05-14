"""Shared helpers for ingesters."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from ..db import connect

log = logging.getLogger(__name__)


@dataclass
class Mention:
    source: str           # 'reddit' | 'twitter'
    source_id: str        # platform-native ID (used for dedup)
    author: str | None
    url: str | None
    posted_at: datetime
    raw_text: str
    engagement_score: int


def upsert_mentions(rows: Iterable[Mention]) -> tuple[int, int]:
    """Bulk-insert mentions, skipping duplicates on (source, source_id).

    Returns (inserted, total_attempted).
    """
    rows = list(rows)
    if not rows:
        return 0, 0

    sql = """
        INSERT INTO mentions (
            source, source_id, author, url, posted_at, raw_text, engagement_score
        ) VALUES (
            %(source)s, %(source_id)s, %(author)s, %(url)s,
            %(posted_at)s, %(raw_text)s, %(engagement_score)s
        )
        ON CONFLICT (source, source_id) DO NOTHING
    """

    inserted = 0
    with connect() as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(sql, {
                "source": r.source,
                "source_id": r.source_id,
                "author": r.author,
                "url": r.url,
                "posted_at": r.posted_at,
                "raw_text": r.raw_text,
                "engagement_score": r.engagement_score,
            })
            inserted += cur.rowcount  # 1 on insert, 0 on conflict
        conn.commit()

    log.info("upsert_mentions: %d inserted / %d total (rest were duplicates)", inserted, len(rows))
    return inserted, len(rows)


def coerce_int(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def coerce_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None
