-- 006_surfaced_leans.sql
-- Lock surfaced leans so they can never silently disappear before grading.
--
-- The dashboard was a purely live re-derivation: a play only showed while it
-- *currently* passed every filter (game_date = today, has a handle_scrape
-- mention, no results row, etc.). Nothing recorded that a lean had ever been
-- shown, so if any of those conditions flipped before tip -- most commonly a
-- premature `results` row from a grader run on a not-yet-final game -- the
-- lean vanished from the board AND from grading, as if it never existed.
--
-- Fix: the first time a play meets the board's display criteria, stamp
-- `surfaced_at`. Once stamped it is NEVER cleared. The board and the grader
-- both key off this stamp, so a surfaced lean stays visible and gradeable no
-- matter what the underlying data does afterward. The bet identity (subject,
-- market, line, side, game_date) is already immutable per play row -- the
-- parser upserts by those columns and only ever bumps counts -- so freezing
-- the stamp is all that's needed to "lock the bet, grow the count".

BEGIN;

ALTER TABLE plays
    ADD COLUMN IF NOT EXISTS surfaced_at TIMESTAMPTZ;

-- The board reads "tonight's surfaced, ungraded leans" — a partial index on
-- the stamped rows keeps that lookup cheap.
CREATE INDEX IF NOT EXISTS plays_surfaced_idx
    ON plays (game_date DESC)
    WHERE surfaced_at IS NOT NULL;

-- Backfill: stamp every play that is eligible for TODAY's board right now, so
-- deploying this migration doesn't blank out tonight's slate. "Today" is in
-- US/Eastern to match how the dashboard and parser anchor game dates. The
-- criteria mirror the default (non-keyword) board query exactly.
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
  );

INSERT INTO _migrations (id) VALUES ('006_surfaced_leans')
ON CONFLICT (id) DO NOTHING;

COMMIT;
