# Phase 2 Plan: Odds API integration + reverse line movement signal

Status: **plan only, no code**. Implementation is **gated on the
game_date accuracy fix** — see "Prerequisites" below.

## Prerequisites (must complete BEFORE any Phase 2 code lands)

The Phase 2 polling schedule (T-2h, T-30m, T-10m, T+0) is computed
relative to game tipoff, which we look up by `event_id` on the play.
If `game_date` is wrong, every downstream poll fires against the wrong
game — strictly worse than no polling. So:

1. **game_date audit must show ≥90% accuracy on a 20-play sample.**
   Today's audit (2026-05-14) showed ~40% accuracy: roughly 60% of
   recent plays have `game_date = posted_ET_date + 1` due to the
   parser using `date.today()` in server UTC instead of an ET-anchored
   "post day". This bug must be fixed first.
2. **Parser day-reference rules** (the prompt update specified in the
   audit response) must be applied: anchor day = posted ET date,
   explicit "tomorrow" / weekday / specific-date overrides, "this
   weekend" / multi-day → reject.
3. **`game_date_unverified` flag** must be implemented (description
   below) and Phase 2 polling must skip any play where it is `TRUE`.

Until all three are done, Phase 2 stays on paper.

### `game_date_unverified` column

```sql
ALTER TABLE plays
    ADD COLUMN game_date_unverified BOOLEAN NOT NULL DEFAULT FALSE;
```

Set to `TRUE` when the parser-assigned `game_date` cannot be matched
against an Odds API event for `(sport, game_date, subject_team)`:

* For team plays: no event exists where `subject` is one of the two
  competitors.
* For player plays: no event exists whose competitors' rosters contain
  `subject`. (Roster lookup is best-effort; if we can't get a roster,
  we leave `game_date_unverified = FALSE` rather than penalize
  ambiguity — the team plays carry the strict check.)

When `TRUE`:

* **`poll_play` MUST return early without firing any odds calls.**
* **`poll_due` MUST exclude these plays from its work queue.**
* Dashboard hides them by default (filter `game_date_unverified = FALSE`).

The flag is settable but not auto-cleared — if the parser later
re-emits the same play with a corrected `game_date`, that's a NEW row
in `plays` (different unique key). The bad row stays flagged for the
audit trail.

## Goal

Combine **brand consensus** (from Phase 1's Twitter handle scraping) with
**reverse line movement** (RLM) from the Odds API to identify true fade
candidates: bets where the recreational public is on one side and the
sharp market is moving the other way.

A play becomes a high-conviction fade when:

1. Multiple brand accounts have pushed the same side, AND
2. The line has moved *against* that side since the play was posted, AND
3. (Bonus) Sharp books (Pinnacle) moved before public books (DK/FD).

## Budget reality

| Constraint | Value |
|--|--|
| Odds API plan | the-odds-api.com Starter, $30/mo |
| Monthly credit allowance | **20,000** |
| Daily budget (30-day month) | **~666 credits/day** |
| Per-event prop pull (all 10 markets, 1 region) | ~10 credits |
| Pinnacle (region=eu, separate call) | +1 credit/event |

**Naive approach** (poll every NBA + MLB game on the slate every 30 min)
burns ~190k credits/month — 9× over budget. We need to be event-driven.

## Trigger strategy

**Event-driven, not scheduled.** Odds polling fires only when:

1. **A new play is parsed from a brand account** → pull `line_at_post`
   for that play's specific (subject, market) pair.
2. **A "tracked" play has aged enough** for a re-poll. We define a
   per-play polling schedule:

   | Time relative to game tipoff | Poll frequency |
   |--|--|
   | T-12h or earlier | once at parse time, then leave alone |
   | T-12h → T-3h | every 2 hours |
   | T-3h → T-30m | every 30 min |
   | T-30m → tipoff | every 10 min |
   | After tipoff | none (final line snapshot) |

A play that gets parsed at T-6h would see ~3 mid-game-day polls + ~6
late polls + 1 close = **~10 polls per play**.

### Credit budget by play volume

| Scenario | Plays/day | Polls/play | Credits/play | Credits/day |
|--|--|--|--|--|
| Slow | 20 | 10 | 11 (10 US + 1 Pinnacle) | 2,200 |
| Typical | 30 | 10 | 11 | 3,300 |
| Heavy | 40 | 10 | 11 | 4,400 |

**Even typical exceeds the 666/day budget by ~5×.** We need cuts:

- **Drop Pinnacle on every poll.** Only pull Pinnacle on the **first** snapshot
  per play (gives us the sharp opener). Saves 1 credit × ~9 polls = 9 credits/play.
- **Reduce per-event markets.** A play has *one* market. Instead of pulling
  all 10 NBA player-prop markets per call, request *just* the specific
  market needed (e.g. only `player_points` for a points prop). The API
  bills per market — so a single-market call costs ~1 credit instead of 10.
- **Per-poll consensus cost = 3 credits.** Consensus is the median of three
  public books: DraftKings + FanDuel + BetMGM. One market × three books =
  3 credits per poll.
- **Differentiated poll schedules by `market_category`** (decision from
  Phase 2 sign-off):

  | market_category | Polls per play | Schedule (relative to tipoff) |
  |--|--|--|
  | `nba_standard`    | 5 | T=parse, T-2h, T-30m, T-10m, T+0 |
  | `mlb_pitcher`     | 5 | T=parse, T-2h, T-30m, T-10m, T+0 |
  | `mlb_batter`      | 3 | T-3h, T-30m, T+0 (skip earlier — lines often not posted) |

Revised math:
- NBA / pitcher plays: 5 polls × 3 books = **15 credits/play** + 1 Pinnacle opener = **16**
- MLB batter plays: 3 polls × 3 books = **9 credits/play** + 1 Pinnacle opener = **10**

Mixed daily mix estimate (15 NBA+pitcher, 15 MLB batter): 15×16 + 15×10 = **390 credits/day ≈ 11,700/month**, ~58% of cap. Heavier days (40 plays) still fit comfortably under 20k.

## Architecture

### New table: `prop_lines`

```sql
CREATE TABLE prop_lines (
    id              BIGSERIAL PRIMARY KEY,
    sport           TEXT NOT NULL,
    game_date       DATE NOT NULL,
    event_id        TEXT NOT NULL,      -- the-odds-api event id
    subject         TEXT,                -- player name (NULL for team markets)
    market          TEXT NOT NULL,       -- 'points', 'spread', 'total', etc.
    line            NUMERIC(7,2),
    side            TEXT NOT NULL,       -- 'over' | 'under' | 'plus' | 'minus' | 'ml_home' | 'ml_away'
    odds            INTEGER,             -- American
    bookmaker       TEXT NOT NULL,       -- 'fanduel', 'draftkings', 'pinnacle', ...
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sport, event_id, subject, market, line, side, bookmaker, captured_at)
);

CREATE INDEX prop_lines_lookup_idx ON prop_lines
    (sport, game_date, subject, market, captured_at DESC);
```

Each snapshot is a row. Time series of lines for a (subject, market) is
recovered by `ORDER BY captured_at`. Dedupe constraint ensures duplicate
captures within the same second don't double-insert.

### New columns on `plays`

```sql
ALTER TABLE plays
    ADD COLUMN event_id        TEXT,            -- the-odds-api event id, set at first odds fetch
    ADD COLUMN market_category TEXT,            -- 'nba_standard' | 'mlb_pitcher' | 'mlb_batter'
    ADD COLUMN line_at_post    NUMERIC(7,2),    -- consensus line at first poll (NULL for moneylines)
    ADD COLUMN line_current    NUMERIC(7,2),    -- consensus line at most recent poll
    ADD COLUMN line_delta      NUMERIC(7,2),    -- line_current - line_at_post
    ADD COLUMN odds_at_post    INTEGER,         -- consensus American odds at first poll
    ADD COLUMN odds_current    INTEGER,         -- consensus American odds at most recent poll
    ADD COLUMN odds_delta_cents INTEGER;        -- odds_current - odds_at_post, in American odds "cents"
                                                -- e.g. +150 → +170 = +20 cents (line moved against backer)
                                                -- Primary RLM signal for moneylines.
```

`line_at_post` is set once when the play is first polled. `line_current`
and `line_delta` get updated on every subsequent poll.

**Moneyline handling** (decision from Phase 2 sign-off): for `market='moneyline'`
plays, `line_*` columns are NULL and the RLM signal flows through
`odds_at_post` / `odds_current` / `odds_delta_cents`. The delta is the
straight American-odds difference. A `+plus` backer "wins" the move when
odds get longer (more positive), so a positive `odds_delta_cents` on a
`+plus` side = line moved *toward* the bet (not RLM). RLM for moneylines
is the opposite: odds shortened against the side the brands took.

"Consensus line" = median across the 3 main public books (DK, FD, BetMGM).
Pinnacle is captured separately into `prop_lines` for sharp reference but
doesn't drive `line_at_post` / `line_current` — those track public.

### `market_category` assignment

Set at parse time, derived from `(sport, market, subject)`:

| Conditions | market_category |
|--|--|
| sport = 'nba' | `nba_standard` |
| sport = 'mlb' AND market IN ('strikeouts_pitcher', 'outs_recorded', 'earned_runs', 'innings_pitched') | `mlb_pitcher` |
| sport = 'mlb' AND market IN ('hits', 'total_bases', 'home_runs', 'runs', 'rbis', 'walks', 'stolen_bases', 'strikeouts_batter', 'hits_runs_rbis') | `mlb_batter` |
| sport = 'mlb' AND market IN ('spread', 'total', 'moneyline', 'run_line') | `mlb_pitcher` (team-level; polls fire on tighter pitcher schedule since pitcher news drives the line) |

This drives both the polling schedule (above) and the dashboard's grouping.

### New scheduler: `worker/ingest/odds.py`

Two entry points:

1. **`poll_play(play_id)`** — pull current odds for one play. Called when
   a new play is parsed.
2. **`poll_due()`** — find all tracked plays whose next-poll-time has passed
   per the schedule above, and update them in one Apify-free batch. Run
   inside the orchestrator loop after `parse-mentions`.

A small helper table `prop_polls` tracks the schedule:

```sql
CREATE TABLE prop_polls (
    play_id        BIGINT PRIMARY KEY REFERENCES plays(id) ON DELETE CASCADE,
    next_poll_at   TIMESTAMPTZ NOT NULL,
    polls_done     SMALLINT NOT NULL DEFAULT 0,
    last_polled_at TIMESTAMPTZ
);
```

When a play has `polls_done >= 5` OR the game has tipped off, the row is
deleted (we have all the snapshots we need; subsequent picks for the same
event reuse the same `prop_lines` history).

### Reverse-line-movement view (Pattern A/B/C as a DB VIEW)

Per Phase 2 sign-off: classification lives **in the database as a VIEW**,
not in worker Python. This keeps the rule transparent (a single SQL file
to read), makes it cheap to change thresholds without redeploying the
worker, and lets the dashboard query it directly.

```sql
CREATE VIEW play_fade_signal AS
SELECT
  p.id AS play_id,
  p.subject, p.market, p.line, p.side,
  p.line_at_post,
  p.line_current,
  p.line_delta,
  p.odds_at_post,
  p.odds_current,
  p.odds_delta_cents,
  COUNT(DISTINCT pm.mention_id) AS brand_mention_count,
  -- Reverse line movement = market moved AGAINST the bet's side.
  -- Spread/total: line_delta sign rules.
  -- Moneyline: odds shortened against the side (delta_cents negative for +plus, positive for -minus).
  CASE
    WHEN p.market = 'moneyline' AND p.side = 'plus'  AND p.odds_delta_cents < 0 THEN true
    WHEN p.market = 'moneyline' AND p.side = 'minus' AND p.odds_delta_cents > 0 THEN true
    WHEN p.market != 'moneyline' AND p.side = 'over'  AND p.line_delta < 0 THEN true
    WHEN p.market != 'moneyline' AND p.side = 'under' AND p.line_delta > 0 THEN true
    WHEN p.market != 'moneyline' AND p.side = 'minus' AND p.line_delta > 0 THEN true
    WHEN p.market != 'moneyline' AND p.side = 'plus'  AND p.line_delta < 0 THEN true
    ELSE false
  END AS reverse_line_movement,
  -- Pattern classification (thresholds editable in this VIEW only)
  CASE
    WHEN COUNT(DISTINCT pm.mention_id) >= 3 AND (
           (p.market = 'moneyline' AND (
              (p.side = 'plus'  AND p.odds_delta_cents < 0) OR
              (p.side = 'minus' AND p.odds_delta_cents > 0)
           )) OR
           (p.market != 'moneyline' AND p.line_delta IS NOT NULL AND (
              (p.side = 'over'  AND p.line_delta < 0) OR
              (p.side = 'under' AND p.line_delta > 0) OR
              (p.side = 'minus' AND p.line_delta > 0) OR
              (p.side = 'plus'  AND p.line_delta < 0)
           ))
         )
      THEN 'A'   -- strong fade: brand consensus + RLM
    WHEN COUNT(DISTINCT pm.mention_id) >= 2
      THEN 'B'   -- brand consensus without confirmed RLM (yet)
    ELSE 'C'     -- single-source pick, weak signal
  END AS fade_pattern
FROM plays p
JOIN play_mentions pm ON pm.play_id = p.id
JOIN mentions m ON m.id = pm.mention_id
WHERE m.source_method = 'handle_scrape'
GROUP BY p.id;
```

### Dashboard surfacing

`app/page.tsx` gets a new top section: **Fade candidates (Pattern A)**.
Plays with `fade_pattern='A'`, sorted by `brand_mention_count` desc,
showing: subject, market, line_at_post → line_current (Δ), conviction,
brand mention count, time-to-tipoff.

Pattern B plays show below as a "watch list" — same display but flagged
as not-yet-confirmed.

## Pinnacle: sharp reference, not primary driver

The-odds-api.com markets Pinnacle under `regions=eu`. One Pinnacle pull
per play, at first poll, gives us the **opening sharp number**. Stored
in `prop_lines` with `bookmaker='pinnacle'`. Used for:

- **Sanity check** on the consensus line at post time (if Pinnacle is
  already moved off DK/FD's number, the public number was probably stale).
- **Future signal**: "Pinnacle moved first, public books followed within
  N hours" is a textbook sharp-money pattern. Surface this when both
  movements are captured.

The primary `line_at_post` / `line_current` stay tied to public books
(DK/FD/MGM consensus) because that's what the recreational better sees
and what we're testing the fade thesis against.

## Implementation phases (within Phase 2)

1. **Migration 004** — add `prop_lines`, `prop_polls`, and the new columns on `plays`.
2. **`worker/ingest/odds.py`** — Odds API client + `poll_play` + `poll_due`.
3. **Wire into orchestrator** — after `parse-mentions`, call `poll_play` for any newly-created plays, then `poll_due` for the rest.
4. **Schema check at parse time** — when a new play is created, populate `event_id` if we can resolve it against the day's Odds API events (NBA: ~2-12 events/day, MLB: 10-15).
5. **`play_fade_signal` view** + dashboard sections.
6. **Backfill** — once running, optionally re-fetch `line_at_post` for any existing plays from the last 24h.

## Resolved decisions (from Phase 2 sign-off)

| Question | Resolution |
|--|--|
| Public-books consensus | Median of **DK + FD + BetMGM**. If only 1-2 present for a market, use what's available (median of 1 or 2). 3 credits per poll. |
| Moneyline RLM signal | `odds_delta_cents` (American odds delta). New columns `odds_at_post` / `odds_current` / `odds_delta_cents` on `plays`. Signs flip by side (see VIEW above). |
| MLB batter prop gating | New `market_category` column. `mlb_batter` plays poll on a 3-poll schedule (T-3h, T-30m, T+0) instead of the full 5. Avoids wasted polls on lines that don't exist yet. |
| Yield-based auto-disable | **Manual.** No automation hook. Build a weekly yield report cron (`worker/scripts/weekly_yield.py`) that emails / logs per-handle stats; user toggles `active: false` in YAML when warranted. |
| Pattern A/B/C classifier | **Database VIEW** (`play_fade_signal`), not worker Python. Thresholds editable in one SQL file. |

## Weekly yield report cron (new)

Standalone script run once a week (Sunday 9pm UTC via Railway cron):

```
worker/scripts/weekly_yield.py
```

Computes for each `active: true` handle, over the last 7 days:
- Mentions ingested
- Plays parsed (and yield %)
- Graded plays + W/L/Push
- ROI (assuming -110 odds where not specified)
- Pattern A play count + Pattern A win rate

Output: pretty-printed table to stdout (captured by Railway logs) +
optional Slack webhook later. No DB writes, no actions taken. The
report is purely advisory; the user reads it and decides what to
deactivate.

## Implementation phases (within Phase 2)

0. **Gate check** — confirm prerequisites (≥90% game_date accuracy + parser day-reference rules shipped + `game_date_unverified` plumbed). Do not proceed past this step until verified.
1. **Migration 005** — add `prop_lines`, `prop_polls`, the new columns on `plays` (`event_id`, `market_category`, `line_at_post`, `line_current`, `line_delta`, `odds_at_post`, `odds_current`, `odds_delta_cents`, `game_date_unverified`), and the `play_fade_signal` VIEW. Note: migration 004 (prefilter audit trail: `mentions.is_pick`, `mentions.skip_reason`) lands with the Phase 1 prefilter work, so Phase 2's first DB migration is 005.
2. **`worker/ingest/odds.py`** — Odds API client + `poll_play` + `poll_due`. Reads `market_category` to decide poll cadence. **Both functions MUST short-circuit when `plays.game_date_unverified = TRUE`** — no API calls for unverified dates.
3. **Parser update** — set `market_category` on `plays` at parse time based on the table above.
4. **Wire into orchestrator** — after `parse-mentions`, call `poll_play` for any newly-created plays, then `poll_due` for the rest.
5. **Event-id resolution** — when a new play is created, resolve `event_id` against the day's Odds API events (NBA: ~2-12 events/day, MLB: 10-15). If no match → set `game_date_unverified = TRUE` and skip subsequent polling.
6. **Dashboard sections** — Pattern A "Fade candidates" + Pattern B "Watch list", driven entirely by `play_fade_signal`.
7. **`worker/scripts/weekly_yield.py`** — standalone yield report.
8. **Backfill** — optionally re-fetch `line_at_post` for any existing plays from the last 24h.

---

**Awaiting approval.** No Phase 2 code will be written until you sign off
on this revised plan.
