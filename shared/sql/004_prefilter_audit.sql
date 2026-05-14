-- 004_prefilter_audit.sql
-- Audit trail for the parser pipeline. A mention can fall into one of:
--   * parsed_at IS NULL                           -> queued
--   * parsed_at SET, is_pick = TRUE               -> Haiku extracted >=1 play
--   * parsed_at SET, is_pick = FALSE,
--                    skip_reason = 'wrong_sport'  -> prefilter rejected before Haiku
--   * parsed_at SET, is_pick = FALSE,
--                    skip_reason = 'no_pick'      -> Haiku returned []
--   * parsed_at SET, parse_error IS NOT NULL      -> Haiku/JSON exception
--
-- Don't silently drop. Keep every mention so we can re-classify later if
-- a prefilter rule changes or we expand sport coverage.

BEGIN;

ALTER TABLE mentions
    ADD COLUMN IF NOT EXISTS is_pick     BOOLEAN,
    ADD COLUMN IF NOT EXISTS skip_reason TEXT;

-- Backfill historical rows so post-deploy queries work cleanly.
-- Any mention that was already parsed before this migration: infer
-- is_pick from whether it has any play_mentions rows.
UPDATE mentions m
SET is_pick = CASE
    WHEN EXISTS (SELECT 1 FROM play_mentions pm WHERE pm.mention_id = m.id) THEN TRUE
    ELSE FALSE
  END,
  skip_reason = CASE
    WHEN EXISTS (SELECT 1 FROM play_mentions pm WHERE pm.mention_id = m.id) THEN NULL
    WHEN m.parse_error IS NOT NULL THEN 'parse_error'
    ELSE 'no_pick'
  END
WHERE m.parsed_at IS NOT NULL
  AND m.is_pick IS NULL;

CREATE INDEX IF NOT EXISTS mentions_skip_reason_idx ON mentions (skip_reason)
    WHERE skip_reason IS NOT NULL;

INSERT INTO _migrations (id) VALUES ('004_prefilter_audit')
ON CONFLICT (id) DO NOTHING;

COMMIT;
