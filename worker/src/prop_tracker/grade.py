"""Results grader.

For each play with `game_date` strictly in the past and no row in `results`:
  1. Pull the day's scoreboard from ESPN and find the game.
  2. Pull that game's box score (for player props) or final score (team markets).
  3. Compute actual_stat and decide public_won / push.
  4. Insert into `results`.

Closing line/odds are left NULL for v1; a follow-up will backfill them
from The Odds API.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from . import espn
from .db import connect

log = logging.getLogger(__name__)

# Markets we can grade directly from ESPN box-score keys (NBA).
_NBA_STAT_KEYS: dict[str, list[str]] = {
    "points":   ["points"],
    "rebounds": ["rebounds"],
    "assists":  ["assists"],
    "threes":   ["threePointFieldGoalsMade-threePointFieldGoalsAttempted",
                 "threePointFieldGoalsMade"],
    "steals":   ["steals"],
    "blocks":   ["blocks"],
    "pra":      ["points", "rebounds", "assists"],
    "pa":       ["points", "assists"],
    "pr":       ["points", "rebounds"],
    "ra":       ["rebounds", "assists"],
}

# Markets we can't reliably grade in v1 -> noted as ungradeable.
_UNGRADEABLE_MARKETS = {"fantasy_points", "double_double", "triple_double"}


def _ungraded_plays() -> list[dict[str, Any]]:
    today = date.today()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id, p.sport, p.game_date, p.subject, p.subject_kind,
                   p.market, p.line, p.side, p.public_pct
            FROM plays p
            LEFT JOIN results r ON r.play_id = p.id
            WHERE r.play_id IS NULL
              AND p.game_date < %s
            ORDER BY p.game_date DESC, p.id
            """,
            (today,),
        )
        return list(cur.fetchall())


def _insert_result(
    play_id: int,
    actual_stat: float | None,
    public_won: bool | None,
    push: bool,
    notes: str | None,
) -> None:
    fade_won = None if public_won is None else (not public_won and not push)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO results (
                play_id, actual_stat, public_won, fade_won, push, notes
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (play_id) DO UPDATE SET
                actual_stat = EXCLUDED.actual_stat,
                public_won  = EXCLUDED.public_won,
                fade_won    = EXCLUDED.fade_won,
                push        = EXCLUDED.push,
                notes       = EXCLUDED.notes,
                graded_at   = now()
            """,
            (play_id, actual_stat, public_won, fade_won, push, notes),
        )
        conn.commit()


def _decide_over_under(actual: float, line: float, side: str) -> tuple[bool | None, bool]:
    """Return (public_won, push) for an over/under decision."""
    if actual == line:
        return None, True
    won = (actual > line) if side == "over" else (actual < line)
    return won, False


def _decide_spread(margin: float, line: float, side: str) -> tuple[bool | None, bool]:
    """Spread: side='minus' means -line (favorite), 'plus' means +line (dog).
    Margin is subject-team's final score minus opponent score."""
    # Adjusted margin against the line.
    if side == "minus":
        adj = margin - line     # need margin > line
    else:
        adj = margin + line     # need margin > -line
    if adj == 0:
        return None, True
    return adj > 0, False


def _find_event_and_team(sport: str, game_date: date, subject: str) -> tuple[dict | None, dict | None]:
    events = espn.get_scoreboard(sport, game_date)
    for ev in events:
        team_match = espn.find_team_in_event(ev, subject)
        if team_match is not None:
            return ev, team_match
    return None, None


def _grade_player_prop(play: dict[str, Any]) -> tuple[float | None, bool | None, bool, str | None]:
    sport = play["sport"]
    if sport != "NBA":
        return None, None, False, f"{sport} player props not supported yet"

    market = play["market"]
    if market in _UNGRADEABLE_MARKETS:
        return None, None, False, f"market '{market}' not supported in v1"

    keys = _NBA_STAT_KEYS.get(market)
    if not keys:
        return None, None, False, f"unknown player market '{market}'"

    # Find a game on the date that contains this player. Easier path: look up
    # box scores for every game on the date until we hit a match.
    events = espn.get_scoreboard(sport, play["game_date"])
    stats: dict[str, float | None] | None = None
    for ev in events:
        summary = espn.get_summary(sport, ev["id"])
        stats = espn.find_player_in_summary(summary, play["subject"])
        if stats is not None:
            break

    if stats is None:
        return None, None, False, f"player '{play['subject']}' not found in box scores"

    if stats.get("__did_not_play__"):
        return None, None, False, "player DNP"

    # Sum the requested stat keys (e.g. 'pra' = points + rebounds + assists).
    values: list[float] = []
    for k in keys:
        v = stats.get(k)
        if v is None:
            # Try fallback key names ESPN sometimes uses.
            for alt in stats:
                if isinstance(alt, str) and alt.lower() == k.lower():
                    v = stats[alt]
                    break
        if v is None:
            return None, None, False, f"ESPN missing key '{k}' for player"
        values.append(float(v))
    actual = sum(values)

    line = play.get("line")
    side = play["side"]
    if line is None:
        return actual, None, False, "play has no line; ungradeable"

    public_won, push = _decide_over_under(actual, float(line), side)
    return actual, public_won, push, None


def _grade_team_market(play: dict[str, Any]) -> tuple[float | None, bool | None, bool, str | None]:
    sport = play["sport"]
    market = play["market"]
    side = play["side"]
    line = play.get("line")

    ev, team = _find_event_and_team(sport, play["game_date"], play["subject"])
    if ev is None or team is None:
        return None, None, False, f"team '{play['subject']}' not found in scoreboard"

    # Game completed?
    status = (ev.get("status") or {}).get("type", {}).get("completed")
    if not status:
        return None, None, False, "game not completed"

    if market == "total":
        total = float(team["total_score"])
        if line is None:
            return total, None, False, "total has no line; ungradeable"
        public_won, push = _decide_over_under(total, float(line), side)
        return total, public_won, push, None

    if market == "spread":
        margin = float(team["margin"])
        if line is None:
            return margin, None, False, "spread has no line; ungradeable"
        public_won, push = _decide_spread(margin, float(line), side)
        return margin, public_won, push, None

    if market == "moneyline":
        won = bool(team["won"])
        return float(team["margin"]), won, False, None

    return None, None, False, f"unknown team market '{market}'"


def run() -> None:
    plays = _ungraded_plays()
    log.info("grade: %d ungraded play(s) to process", len(plays))

    graded = 0
    voided = 0
    errors = 0

    for play in plays:
        try:
            if play["subject_kind"] == "player":
                actual, public_won, push, notes = _grade_player_prop(play)
            else:
                actual, public_won, push, notes = _grade_team_market(play)
        except Exception as e:  # noqa: BLE001
            log.exception("grade: play %d crashed: %s", play["id"], e)
            actual, public_won, push, notes = None, None, False, f"grader error: {e!s}"[:480]
            errors += 1

        _insert_result(play["id"], actual, public_won, push, notes)
        if public_won is None:
            voided += 1
            log.info("grade: play %d voided -- %s", play["id"], notes)
        else:
            graded += 1
            result = "PUSH" if push else ("WIN" if public_won else "LOSS")
            log.info(
                "grade: play %d -> %s  actual=%.2f line=%s side=%s",
                play["id"], result, actual, play["line"], play["side"],
            )

    log.info("grade: done. graded=%d voided=%d errors=%d", graded, voided, errors)
