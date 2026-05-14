"""Sport prefilter — runs BEFORE Claude Haiku to drop tweets that are
clearly about sports we don't cover.

Decision rule (from Phase 1 polish sign-off):
  1. If text matches an OTHER_SPORT marker AND does NOT match any
     NBA or MLB marker → SKIP (caller records skip_reason='wrong_sport').
  2. Otherwise → keep, pass to Haiku.

Generic betting terms (spread / moneyline / total / over / under / parlay /
unit / +EV) do NOT count as NBA/MLB markers — they appear across all
sports. Only league names, team names, and sport-specific stat markets
override the OTHER_SPORT match. Player surnames are intentionally NOT in
the marker lists (too many overlap across sports — Jones, Smith, etc.).

Dry-run baseline (last 200 handle_scrape mentions, before deploy):
  total skipped 40/200 (20%); ActionNetworkHQ 3%, Covers 19%,
  VSiNLive 31%, BettingPros 27%. Zero NBA/MLB false-positive skips.
"""
from __future__ import annotations

import re

# Other sports we explicitly don't cover.
# WNBA is intentionally in here — different schema, deferred to later.
OTHER_SPORT = re.compile(
    r"\b("
    r"wnba|nfl|nhl|mls|"
    r"college\s+football|cfb|ncaaf|college\s+basketball|cbb|ncaab|"
    r"soccer|hockey|epl|premier\s+league|la\s+liga|bundesliga|serie\s+a|"
    r"champions\s+league|uefa|fifa|world\s+cup|"
    r"tennis|atp|wta|"
    r"golf|pga|liv\s+golf|masters|us\s+open|"
    r"ufc|mma|boxing|"
    r"f1|formula\s+1|nascar|"
    r"cricket|rugby"
    r")\b",
    re.IGNORECASE,
)

# NBA-specific markers: league name, NBA-only markets, all 30 team names.
NBA_MARKERS = re.compile(
    r"\b("
    r"nba|"
    r"rebounds?|assists?|pra|points\+rebounds|"
    r"3[- ]?pointers?|threes\s+made|3pm|blocks?|steals?|"
    r"lakers|celtics|warriors|nets|knicks|76ers|sixers|bucks|heat|raptors|"
    r"bulls|cavaliers|cavs|pistons|pacers|hawks|hornets|magic|wizards|"
    r"nuggets|timberwolves|wolves|thunder|trail\s+blazers|blazers|jazz|"
    r"clippers|kings|suns|mavericks|mavs|rockets|grizzlies|pelicans|spurs"
    r")\b",
    re.IGNORECASE,
)

# MLB-specific markers: league name, MLB-only markets, all 30 team names.
MLB_MARKERS = re.compile(
    r"\b("
    r"mlb|world\s+series|alds|nlds|alcs|nlcs|wildcard\s+game|"
    r"home\s+runs?|homers?|hrs?|rbis?|total\s+bases|"
    r"strikeouts?|hits?|stolen\s+bases?|walks?|earned\s+runs?|"
    r"innings\s+pitched|outs\s+recorded|"
    r"yankees|red\s+sox|dodgers|braves|astros|orioles|rays|blue\s+jays|"
    r"twins|guardians|tigers|royals|white\s+sox|"
    r"phillies|mets|nationals|marlins|"
    r"brewers|cubs|cardinals|pirates|reds|"
    r"padres|giants|diamondbacks|dbacks|rockies|"
    r"angels|mariners|athletics|rangers"
    r")\b",
    re.IGNORECASE,
)


def should_skip(text: str) -> tuple[bool, str | None]:
    """Return (skip, reason).

    reason is a short tag for logging. Currently only 'wrong_sport' is
    emitted, but future rules (e.g. 'too_short', 'image_only') would
    add new tags here.
    """
    if not text:
        return False, None
    other = OTHER_SPORT.search(text)
    if not other:
        return False, None  # no foreign-sport signal; let Haiku decide
    if NBA_MARKERS.search(text) or MLB_MARKERS.search(text):
        return False, None  # mixed-sport tweet; NBA/MLB content present
    return True, "wrong_sport"
