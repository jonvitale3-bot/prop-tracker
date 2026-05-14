-- 001_init.sql
-- Initial schema for prop-tracker.
-- Tables: mentions, plays, play_mentions, results, plus _migrations bookkeeping.

BEGIN;

CREATE TABLE IF NOT EXISTS _migrations (
    id          TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── mentions ──────────────────────────────────────────────
-- Raw posts pulled from Reddit / Twitter. Deduped by (source, source_id).
CREATE TABLE mentions (
    id                BIGSERIAL PRIMARY KEY,
    source            TEXT        NOT NULL CHECK (source IN ('reddit', 'twitter')),
    source_id         TEXT        NOT NULL,
    author            TEXT,
    url               TEXT,
    posted_at         TIMESTAMPTZ NOT NULL,
    raw_text          TEXT        NOT NULL,
    engagement_score  INTEGER     NOT NULL DEFAULT 0,
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    parsed_at         TIMESTAMPTZ,  -- null = not yet parsed
    parse_error       TEXT,         -- non-null if parser threw on this mention
    UNIQUE (source, source_id)
);

CREATE INDEX mentions_posted_at_idx ON mentions (posted_at DESC);
CREATE INDEX mentions_unparsed_idx  ON mentions (id) WHERE parsed_at IS NULL;

-- ── plays ─────────────────────────────────────────────────
-- A distinct bet (subject + market + line + side on a given game_date).
-- Each side of a bet is a separate row; public_pct compares this row's
-- mention_count against the count on the opposing side(s) of the same bet.
CREATE TABLE plays (
    id                BIGSERIAL PRIMARY KEY,
    sport             TEXT      NOT NULL CHECK (sport IN ('NBA', 'MLB')),
    game_date         DATE      NOT NULL,
    subject           TEXT      NOT NULL,   -- player name or team name
    subject_kind      TEXT      NOT NULL CHECK (subject_kind IN ('player', 'team')),
    market            TEXT      NOT NULL,   -- 'points', 'rebounds', 'spread', 'moneyline', 'total', etc.
    line              NUMERIC(7,2),         -- nullable for moneyline
    side              TEXT      NOT NULL,   -- 'over', 'under', 'home', 'away', 'plus', 'minus', 'ml_home', 'ml_away'
    mention_count     INTEGER   NOT NULL DEFAULT 0,
    public_pct        NUMERIC(5,2),         -- 0..100, this side's share of mentions on the bet
    avg_conviction    NUMERIC(3,2),         -- 1..5
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Uniqueness: same bet+side can't have two rows. line is nullable, so we use a
-- COALESCE-based expression index (NULLs are otherwise treated as distinct).
CREATE UNIQUE INDEX plays_unique_idx ON plays (
    sport, game_date, subject, market, COALESCE(line, -99999), side
);
CREATE INDEX plays_game_date_idx ON plays (game_date DESC);

-- ── play_mentions ─────────────────────────────────────────
-- Which mentions support which plays, with per-mention metadata captured
-- at parse time (conviction, sportsbook, odds, raw quote).
CREATE TABLE play_mentions (
    play_id      BIGINT NOT NULL REFERENCES plays(id)    ON DELETE CASCADE,
    mention_id   BIGINT NOT NULL REFERENCES mentions(id) ON DELETE CASCADE,
    conviction   SMALLINT NOT NULL CHECK (conviction BETWEEN 1 AND 5),
    sportsbook   TEXT,                -- 'DK', 'FD', 'MGM', etc. nullable.
    odds_at_post INTEGER,             -- American odds: -110, +145. nullable.
    raw_quote    TEXT,                -- excerpt from the mention that triggered the parse
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (play_id, mention_id)
);

CREATE INDEX play_mentions_mention_idx ON play_mentions (mention_id);

-- ── results ───────────────────────────────────────────────
-- Graded outcome for a play. Closing line/odds come from The Odds API;
-- actual_stat comes from ESPN box scores.
CREATE TABLE results (
    play_id       BIGINT PRIMARY KEY REFERENCES plays(id) ON DELETE CASCADE,
    closing_line  NUMERIC(7,2),
    closing_odds  INTEGER,            -- American
    actual_stat   NUMERIC(7,2),       -- realized number (points scored, runs, margin, etc.)
    public_won    BOOLEAN,            -- did this side win?
    fade_won      BOOLEAN,            -- did fading this side win?
    push          BOOLEAN NOT NULL DEFAULT FALSE,
    graded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes         TEXT
);

INSERT INTO _migrations (id) VALUES ('001_init');

COMMIT;
