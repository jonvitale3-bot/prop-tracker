# Backlog / known issues

Things we've intentionally deferred. Pick up when relevant.

## Dashboard `game_date` filter vs mention lookback
`getTonightsPlays` and `getHotSubjects` filter `plays.game_date = today`, but
the ingester now pulls **48 hours** of tweets per handle. A pick parsed
from a tweet posted yesterday could land with `game_date=yesterday` (Claude
infers game_date from the tweet text + posted_at). Those plays correctly
won't appear in today's dashboard view — but if the parser ever sets
game_date=NULL or guesses wrong, last-night's picks could leak into the
"tonight" view.

Action: spot-check by manually inspecting any play whose `mention.posted_at`
is more than 24h before `play.game_date`. If we see frequent leaks, tighten
the parser prompt's date-handling.

## Twitter zero-volume handles
After ~1 week of clean handle_scrape data, audit
`HeedTheseTakes / CoversExperts / VegasInsider / Hardwood_Paroxysm / erinkatedolan /
catburglarpicks / AlexMonahan100` — if they're still returning 0 over 48h
windows, swap them in `worker/config/twitter_accounts.yaml`.

## `twitter_queries.py` cleanup
File kept on disk but no longer imported. Delete after a week of clean
handle_scrape data confirms we don't need the fallback.

## Closing line backfill
`ODDS_API_KEY` is wired but unused. Grader scores against the line captured
at post time. Follow-up: fetch closing lines from The Odds API at game-start
time and backfill `results.closing_line` + `results.closing_odds`.

## Ungradeable markets
`fantasy_points`, `double_double`, `triple_double` parser-extracts succeed
but grader flags as ungradeable in v1. Implement these when they show up
with meaningful volume in `plays`.

## MLB support
NBA only for now. Grader has `_SPORT_PATHS["MLB"]` wired into the ESPN
client, but no MLB-specific player-stat keys. Add `_MLB_STAT_KEYS` map
(hits / runs / strikeouts / etc.) and Twitter handle list for MLB once
the season picks up.
