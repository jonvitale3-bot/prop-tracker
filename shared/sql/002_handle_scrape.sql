-- 002_handle_scrape.sql
-- Switch Twitter ingestion from keyword search to per-handle scraping.
-- Add provenance + tier columns to mentions so we can distinguish old
-- keyword-sourced data from the new handle_scrape pipeline.

BEGIN;

ALTER TABLE mentions
    ADD COLUMN IF NOT EXISTS source_method TEXT NOT NULL DEFAULT 'keyword_search';

ALTER TABLE mentions
    ADD COLUMN IF NOT EXISTS author_tier SMALLINT;

-- All existing rows are pre-handle-scrape data. The DEFAULT handled new inserts
-- during column creation, but be explicit so future re-runs are no-ops.
UPDATE mentions SET source_method = 'keyword_search'
WHERE source_method IS NULL OR source_method = '';

CREATE INDEX IF NOT EXISTS mentions_source_method_idx
    ON mentions (source_method);

INSERT INTO _migrations (id) VALUES ('002_handle_scrape')
ON CONFLICT (id) DO NOTHING;

COMMIT;
