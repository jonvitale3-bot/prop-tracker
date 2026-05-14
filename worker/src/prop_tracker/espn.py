"""ESPN unofficial JSON API client.

Endpoints used:
  scoreboard:  /apis/site/v2/sports/{sportPath}/scoreboard?dates=YYYYMMDD
  summary:     /apis/site/v2/sports/{sportPath}/summary?event={id}

No auth required. Rate limits are generous for personal use.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Map sport short name -> ESPN URL path.
_SPORT_PATHS = {
    "NBA": "basketball/nba",
    "MLB": "baseball/mlb",
}

_RETRY = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
)


@_RETRY
def get_scoreboard(sport: str, game_date: date) -> list[dict[str, Any]]:
    """Return the list of `events` (games) for that date."""
    path = _SPORT_PATHS[sport]
    url = f"{_BASE}/{path}/scoreboard"
    params = {"dates": game_date.strftime("%Y%m%d")}
    with httpx.Client(timeout=15.0) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    return data.get("events", [])


@_RETRY
def get_summary(sport: str, event_id: str) -> dict[str, Any]:
    """Return the full summary JSON for a given event id (includes box score)."""
    path = _SPORT_PATHS[sport]
    url = f"{_BASE}/{path}/summary"
    with httpx.Client(timeout=15.0) as client:
        r = client.get(url, params={"event": event_id})
        r.raise_for_status()
        return r.json()


# ---- helpers ---------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def _norm(name: str) -> set[str]:
    """Normalize a name to a set of lowercase tokens for fuzzy matching."""
    s = _NON_ALNUM.sub(" ", name.lower())
    return {t for t in s.split() if t}


def find_team_in_event(event: dict[str, Any], team_name: str) -> dict[str, Any] | None:
    """Locate the competitor entry for `team_name` (fuzzy) in an event.
    Returns the competitor dict with extra keys: `is_home`, `final_score`,
    `opponent_score`, `won`, or None if no match."""
    target = _norm(team_name)
    if not target:
        return None
    comps = event.get("competitions", [{}])[0].get("competitors", [])
    home = next((c for c in comps if c.get("homeAway") == "home"), None)
    away = next((c for c in comps if c.get("homeAway") == "away"), None)
    if home is None or away is None:
        return None

    def _candidates(c: dict[str, Any]) -> list[set[str]]:
        team = c.get("team", {})
        return [
            _norm(team.get("displayName", "")),
            _norm(team.get("shortDisplayName", "")),
            _norm(team.get("name", "")),
            _norm(team.get("nickname", "")),
            _norm(team.get("abbreviation", "")),
            _norm(team.get("location", "")),
        ]

    def _match(c: dict[str, Any]) -> bool:
        for cand in _candidates(c):
            if cand and (target.issubset(cand) or cand.issubset(target)):
                return True
        return False

    chosen = home if _match(home) else (away if _match(away) else None)
    if chosen is None:
        return None
    other = away if chosen is home else home
    try:
        s = float(chosen.get("score") or 0)
        o = float(other.get("score") or 0)
    except (TypeError, ValueError):
        s = o = 0.0
    return {
        **chosen,
        "is_home": chosen.get("homeAway") == "home",
        "final_score": s,
        "opponent_score": o,
        "margin": s - o,
        "total_score": s + o,
        "won": s > o,
        "opponent": other,
    }


def find_player_in_summary(
    summary: dict[str, Any], player_name: str
) -> dict[str, float | None] | None:
    """Return a dict of `{stat_key: numeric}` for the matching player, or None.

    NBA boxscore.players is a list with one entry per team; each has
    `statistics` with `keys` (the column ids) and `athletes` (rows).
    Returns common keys like points, rebounds, assists, threePointFieldGoalsMade.
    """
    target = _norm(player_name)
    if not target:
        return None

    boxscore = summary.get("boxscore") or {}
    team_groups = boxscore.get("players") or []

    matched_player_stats: dict[str, float | None] | None = None
    ambiguous = False

    for group in team_groups:
        for stats_table in group.get("statistics", []):
            keys = stats_table.get("keys") or stats_table.get("names") or []
            for ath in stats_table.get("athletes") or []:
                a = ath.get("athlete", {})
                name_tokens = _norm(a.get("displayName") or a.get("fullName") or "")
                # Match if every token in target appears in name_tokens (covers
                # "Tatum" -> "Jayson Tatum") or names overlap heavily.
                if name_tokens and target.issubset(name_tokens):
                    stats_list = ath.get("stats") or []
                    parsed: dict[str, float | None] = {}
                    for k, v in zip(keys, stats_list):
                        parsed[k] = _parse_stat(k, v)
                    parsed["__did_not_play__"] = ath.get("didNotPlay", False)  # type: ignore[assignment]
                    parsed["__display_name__"] = a.get("displayName")          # type: ignore[assignment]
                    if matched_player_stats is None:
                        matched_player_stats = parsed
                    elif parsed["__display_name__"] != matched_player_stats.get("__display_name__"):
                        ambiguous = True

    if ambiguous:
        log.warning(
            "ESPN: ambiguous player match for %r; declining to grade", player_name
        )
        return None
    return matched_player_stats


def _parse_stat(key: str, raw: Any) -> float | None:
    """Coerce ESPN's box-score values to a number. Handles 'X-Y' (e.g.
    '2-7' for threes made-attempted) by returning the first part."""
    if raw is None or raw == "--":
        return None
    s = str(raw).strip()
    if not s or s == "--":
        return None
    if "-" in s:
        s = s.split("-", 1)[0]
    try:
        return float(s)
    except ValueError:
        return None
