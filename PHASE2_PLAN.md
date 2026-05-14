# Phase 2 Plan: Odds API integration + reverse line movement signal

Status: **plan only, no code**. Awaiting review before implementation.

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
- **Cap polls per play at 5** (T=parse, T-2h, T-30m, T-10m, T+0). Loses some
  resolution but is good enough for RLM signal.

Revised math: **30 plays/day × 5 polls × ~1.5 credits avg = ~225 credits/day = ~6,750/month**, well under budget. Pinnacle costs add ~30 plays × 1 = 30 credits/day for the opener pulls. **Total budget: ~7,000 credits/month, ~35% of cap.** Plenty of headroom for spikes.

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
    ADD COLUMN event_id      TEXT,            -- the-odds-api event id, set at first odds fetch
    ADD COLUMN line_at_post  NUMERIC(7,2),    -- consensus line at first poll
    ADD COLUMN line_current  NUMERIC(7,2),    -- consensus line at most recent poll
    ADD COLUMN line_delta    NUMERIC(7,2);    -- line_current - line_at_post
```

`line_at_post` is set once when the play is first polled. `line_current`
and `line_delta` get updated on every subsequent poll.

"Consensus line" = median across the 3 main public books (DK, FD, BetMGM).
Pinnacle is captured separately into `prop_lines` for sharp reference but
doesn't drive `line_at_post` / `line_current` — those track public.

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

### Reverse-line-movement view

A derived view computes the fade-pattern classification:

```sql
CREATE VIEW play_fade_signal AS
SELECT
  p.id AS play_id,
  p.subject, p.market, p.line, p.side,
  p.line_at_post,
  p.line_current,
  p.line_delta,
  COUNT(DISTINCT pm.mention_id) AS brand_mention_count,
  -- Reverse line movement = line moved AGAINST the bet's side
  -- (over bet → line went down, under bet → line went up, etc.)
  CASE
    WHEN p.side = 'over'  AND p.line_delta <  0 THEN true
    WHEN p.side = 'under' AND p.line_delta >  0 THEN true
    WHEN p.side = 'minus' AND p.line_delta > 0 THEN true  -- favorite line shrunk = sharp moved off favorite
    WHEN p.side = 'plus'  AND p.line_delta < 0 THEN true
    ELSE false
  END AS reverse_line_movement,
  -- Pattern classification
  CASE
    WHEN COUNT(DISTINCT pm.mention_id) >= 3 AND p.line_delta IS NOT NULL
         AND ((p.side='over'  AND p.line_delta <  0) OR
              (p.side='under' AND p.line_delta >  0) OR
              (p.side='minus' AND p.line_delta > 0) OR
              (p.side='plus'  AND p.line_delta < 0))
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

## Open questions before implementation

- **Which public books for consensus?** Default proposal: median of DK / FD / BetMGM. If only one is present for a market, use that. Alternative: just DK (simpler, single source of truth).
- **What does "consensus line" mean for moneylines?** No line value — only price (odds). For ML plays, `line_at_post` is NULL but `odds_at_post` (already on `play_mentions`) is the comparison anchor; `line_delta` becomes odds delta in cents.
- **MLB batter props gating** — recon showed these often aren't posted until close to first pitch. Should we skip Odds API polls for MLB batter plays parsed > 4h before tipoff, and let the first poll happen at T-3h?
- **Auto-disable handles below 5% yield** — automation hook, or always manual via the per-account log? My take: manual for now (you mentioned this in the Phase 1 polish note), automate later if it's annoying.

---

When you've reviewed, we can refine the credit budget (especially the
single-market trick) and the schedule. Ready when you are.
