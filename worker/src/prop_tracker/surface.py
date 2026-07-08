"""Pin surfaced leans so they can't disappear before grading.

Runs every orchestrator cycle, right after parsing. Any play that currently
meets the board's default display criteria gets `surfaced_at` stamped the
first time it qualifies; the stamp is never cleared. From then on the
dashboard and grader key off the stamp, so a lean that was shown to users
stays visible and gradeable even if the underlying data later changes (a
mention re-classified, a premature grade, a game_date edge case, etc.).

Idempotent: only stamps rows where surfaced_at IS NULL, so re-running is a
no-op for already-pinned plays.
"""

from __future__ import annotations

import logging

from .db import connect

log = logging.getLogger(__name__)

# Mirrors the default (non-keyword) board query in lib/queries.ts:
#   game_date = today (ET) · player prop · not legacy · not yet graded ·
#   supported by at least one handle_scrape mention.
_PIN_SQL = """
    UPDATE plays p
    SET surfaced_at = now()
    WHERE p.surfaced_at IS NULL
      AND p.game_date = (now() AT TIME ZONE 'America/New_York')::date
      AND p.subject_kind = 'player'
      AND p.legacy_game_date = FALSE
      AND NOT EXISTS (SELECT 1 FROM results r WHERE r.play_id = p.id)
      AND EXISTS (
          SELECT 1
          FROM play_mentions pm
          JOIN mentions m ON m.id = pm.mention_id
          WHERE pm.play_id = p.id
            AND m.source_method = 'handle_scrape'
      )
"""


def run() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(_PIN_SQL)
        pinned = cur.rowcount
        conn.commit()
    if pinned:
        log.info("surface: pinned %d newly-surfaced lean(s) for tonight", pinned)
    else:
        log.info("surface: no new leans to pin")
