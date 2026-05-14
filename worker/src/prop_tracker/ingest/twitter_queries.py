"""Twitter search-query builder.

Combines three sources to maximise coverage of betting Twitter:

  1. STAR_PLAYERS: a curated list of high-volume NBA names (the kind
     gambling Twitter uses by surname only \u2014 \"Tatum over\", \"Wemby u25\").
     Filtered each cycle to players on teams that have a game *today*,
     using ESPN's scoreboard.
  2. STATIC_KEYWORDS: gambling-Twitter slang independent of player
     (\"nba lock\", \"nba prop\", \"over under nba\").
  3. TWITTER_QUERIES env var (optional): user-provided extras appended
     verbatim.

The Twitter actor accepts an array of search strings, so each entry
becomes its own search. Output is deduped, trimmed to MAX_QUERIES.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Iterable

from .. import espn

log = logging.getLogger(__name__)

MAX_QUERIES = int(os.environ.get("TWITTER_MAX_QUERIES", "12"))

# Curated NBA stars (by surname / nickname \u2014 the form Twitter uses).
# Keyed by team abbreviation; we filter to only the teams playing tonight.
# Easy to extend: add (team_abbr, [names]).
STAR_PLAYERS: dict[str, list[str]] = {
    "BOS": ["Tatum", "Brown", "Holiday"],
    "NYK": ["Brunson", "Towns", "Anunoby"],
    "MIL": ["Giannis", "Lillard"],
    "CLE": ["Mitchell", "Mobley", "Garland"],
    "DET": ["Cunningham", "Ivey"],
    "PHI": ["Embiid", "Maxey", "George"],
    "ORL": ["Banchero", "Wagner"],
    "MIA": ["Butler", "Adebayo"],
    "ATL": ["Trae", "Young", "Murray"],
    "IND": ["Haliburton", "Siakam"],
    "CHI": ["LaVine", "DeRozan", "Vucevic"],
    "TOR": ["Barnes", "Quickley"],
    "DEN": ["Jokic", "Murray", "MPJ"],
    "OKC": ["SGA", "Holmgren"],
    "MIN": ["Edwards", "Randle", "Gobert"],
    "LAL": ["LeBron", "Bron", "AD"],
    "GSW": ["Curry", "Klay"],
    "DAL": ["Luka", "Irving"],
    "PHX": ["Booker", "Durant", "KD", "Beal"],
    "LAC": ["Kawhi", "Harden"],
    "MEM": ["Morant", "Jackson"],
    "NOP": ["Zion", "Murphy", "McCollum"],
    "SAS": ["Wembanyama", "Wemby", "Fox", "Castle"],
    "HOU": ["Sengun", "Green"],
    "POR": ["Sharpe", "Henderson"],
    "SAC": ["Sabonis", "DeRozan", "Fox"],
    "UTA": ["Markkanen"],
    "WAS": ["Poole", "Sarr"],
    "CHA": ["LaMelo", "Miller"],
    "BKN": ["Bridges", "Thomas"],
}

STATIC_KEYWORDS: list[str] = [
    "nba prop",
    "nba lock",
    "nba over under",
    "playoff prop",
    "nba bet tonight",
    "nba props tonight",
]


def _teams_playing_today(today: date) -> set[str]:
    try:
        events = espn.get_scoreboard("NBA", today)
    except Exception:
        log.exception("twitter_queries: failed to fetch ESPN scoreboard; using all teams")
        return set(STAR_PLAYERS.keys())

    abbrs: set[str] = set()
    for ev in events:
        for c in ev.get("competitions", [{}])[0].get("competitors", []):
            abbr = (c.get("team") or {}).get("abbreviation")
            if abbr:
                abbrs.add(abbr.upper())
    if not abbrs:
        log.warning("twitter_queries: no teams found on ESPN for %s; using all star names", today)
        return set(STAR_PLAYERS.keys())
    log.info("twitter_queries: teams playing %s: %s", today, sorted(abbrs))
    return abbrs


def _player_queries(teams: Iterable[str]) -> list[str]:
    out: list[str] = []
    for team in teams:
        for name in STAR_PLAYERS.get(team, []):
            out.append(f"{name} over")
            out.append(f"{name} under")
    return out


def _env_extras() -> list[str]:
    raw = os.environ.get("TWITTER_QUERIES", "")
    return [q.strip() for q in raw.split(",") if q.strip()]


def build_queries() -> list[str]:
    """Return the final, deduplicated list of search terms for today."""
    today = date.today()
    teams_today = _teams_playing_today(today)

    queries: list[str] = []
    queries.extend(_player_queries(teams_today))
    queries.extend(STATIC_KEYWORDS)
    queries.extend(_env_extras())

    # Dedupe while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(q)

    if len(deduped) > MAX_QUERIES:
        log.info("twitter_queries: trimming %d -> %d (cap=MAX_QUERIES)",
                 len(deduped), MAX_QUERIES)
        deduped = deduped[:MAX_QUERIES]

    log.info("twitter_queries: built %d queries: %s",
             len(deduped), " | ".join(deduped))
    return deduped
