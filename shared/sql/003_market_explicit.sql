-- 003_market_explicit.sql
-- Track per-mention whether the market was stated explicitly in the
-- tweet or inferred (e.g. from line magnitude). Drives the dashboard's
-- ability to downweight low-confidence inferred extractions.

BEGIN;

ALTER TABLE play_mentions
    ADD COLUMN IF NOT EXISTS market_explicit BOOLEAN NOT NULL DEFAULT TRUE;

-- Existing rows default to TRUE (column DEFAULT). They were captured under
-- the v1 prompt which didn't expose explicit/inferred — assume explicit
-- so we don't accidentally downweight historical extractions.

INSERT INTO _migrations (id) VALUES ('003_market_explicit')
ON CONFLICT (id) DO NOTHING;

COMMIT;
