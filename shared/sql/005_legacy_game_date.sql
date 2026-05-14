-- 005_legacy_game_date.sql
-- Quarantine plays parsed before the game_date fix landed.
--
-- The pre-fix parser used date.today() in server UTC as the "today"
-- anchor passed to Haiku. For tweets posted in the evening ET, that
-- meant Haiku saw "today" as the next calendar day and assigned
-- game_date one day too late. Audit (2026-05-14) found ~60% of recent
-- plays exhibited this drift.
--
-- We are not backfilling: too few legacy plays to be worth the cost,
-- and a re-parse can't recover "tomorrow" / weekday references that
-- have since aged out. Instead we mark every play that exists right
-- now as legacy, and the dashboard + fade signal + Phase 2 polling
-- ignore them.
--
-- Every play inserted by the post-fix parser will default to
-- legacy_game_date = FALSE and flow through normally.

BEGIN;

ALTER TABLE plays
    ADD COLUMN IF NOT EXISTS legacy_game_date BOOLEAN NOT NULL DEFAULT FALSE;

-- One-shot quarantine: every play that exists at migration time is legacy.
-- Safe to run multiple times (idempotent because the column default is FALSE
-- for new rows, and we only flip existing rows where the flag is still FALSE).
UPDATE plays
SET legacy_game_date = TRUE
WHERE legacy_game_date = FALSE;

CREATE INDEX IF NOT EXISTS plays_active_game_date_idx
    ON plays (game_date DESC)
    WHERE legacy_game_date = FALSE;

INSERT INTO _migrations (id) VALUES ('005_legacy_game_date')
ON CONFLICT (id) DO NOTHING;

COMMIT;
