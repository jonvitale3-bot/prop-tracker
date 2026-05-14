"""Diagnostic: audit parser-assigned game_date vs reality.

Pulls 20 random plays from the last 7 days, joins to the source mention,
and for each one:
  - shows tweet posted_at (UTC + ET), parser game_date, and raw_text
  - hits ESPN scoreboard for that sport on game_date to see if games exist
  - if subject_kind='team', checks whether the team appears in the day's games
  - if subject_kind='player', best-effort: checks whether any team appearing
    in the day's games is also mentioned in raw_text (partial verification)

Output is a single table + a summary count. No DB writes. Delete after the
game-date issue is resolved.

Usage (from repo root):
    cd worker && .venv/bin/python scripts/game_date_audit.py
"""
from __future__ import annotations

import os
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=True)

import psycopg
import urllib.request
import json
from psycopg.rows import dict_row

DSN = os.environ["DATABASE_URL"]
ET = ZoneInfo("America/New_York")

ESPN_URLS = {
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={d}",
    "MLB": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={d}",
}


def fetch_espn(sport: str, game_date: str) -> dict:
    d = game_date.replace("-", "")
    url = ESPN_URLS[sport].format(d=d)
    req = urllib.request.Request(url, headers={"User-Agent": "prop-tracker-audit"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def teams_on_date(espn_payload: dict) -> list[tuple[str, str]]:
    """Returns list of (display_name, short_display_name) for both competitors of every event."""
    out: list[tuple[str, str]] = []
    for ev in espn_payload.get("events", []):
        for comp in ev.get("competitions", []):
            for c in comp.get("competitors", []):
                team = c.get("team", {})
                out.append((team.get("displayName", ""), team.get("shortDisplayName", "")))
    return out


def verify_play(sport: str, subject: str, subject_kind: str, raw_text: str, game_date: str) -> str:
    try:
        payload = fetch_espn(sport, game_date)
    except Exception as e:
        return f"espn-error: {e}"
    teams = teams_on_date(payload)
    if not teams:
        return "NO GAMES that day"
    if subject_kind == "team":
        # Match team subject against the day's teams (loose substring).
        subj_l = subject.lower()
        for full, short in teams:
            if not full and not short:
                continue
            if full.lower() in subj_l or subj_l in full.lower():
                return f"OK (team in slate: {full})"
            if short and (short.lower() in subj_l or subj_l in short.lower()):
                return f"OK (team in slate: {short})"
        return f"TEAM NOT ON SLATE (slate had {len(teams)//2} games)"
    # Player play: best-effort. Confirm any slate team appears in raw_text.
    matches = []
    raw_l = raw_text.lower() if raw_text else ""
    for full, short in teams:
        if full and full.lower() in raw_l:
            matches.append(full)
        elif short and short.lower() in raw_l:
            matches.append(short)
    if matches:
        return f"PROB OK (player; raw_text mentions slate teams: {', '.join(set(matches[:3]))})"
    return f"UNVERIFIED (player; no slate team named in tweet; slate had {len(teams)//2} games)"


def main() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with psycopg.connect(DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id AS play_id, p.sport, p.game_date::text AS game_date,
                   p.subject, p.subject_kind, p.market, p.side, p.line,
                   m.id AS mention_id, m.author, m.posted_at, m.raw_text
            FROM plays p
            JOIN play_mentions pm ON pm.play_id = p.id
            JOIN mentions m ON m.id = pm.mention_id
            WHERE m.source_method = 'handle_scrape'
              AND m.posted_at >= %s::timestamptz
            ORDER BY random()
            LIMIT 20
            """,
            (cutoff,),
        )
        rows = cur.fetchall()

    if not rows:
        print("No plays found in the last 7 days from handle_scrape mentions.")
        return

    print(f"Sampled {len(rows)} plays from the last 7 days.\n")
    print("=" * 110)

    bad = 0
    unverified = 0
    ok = 0

    for i, r in enumerate(rows, 1):
        posted_utc = r["posted_at"]
        posted_et = posted_utc.astimezone(ET)
        play_str = f"{r['subject']} {r['market']} {r['side']}"
        if r["line"] is not None:
            play_str += f" {r['line']}"
        verdict = verify_play(
            r["sport"], r["subject"], r["subject_kind"], r["raw_text"] or "", r["game_date"]
        )
        if verdict.startswith("OK") or verdict.startswith("PROB OK"):
            ok += 1
        elif verdict.startswith("UNVERIFIED"):
            unverified += 1
        else:
            bad += 1

        print(f"[{i:>2}] play {r['play_id']}: {r['sport']} {play_str}")
        print(f"     posted: {posted_utc.isoformat()} UTC  ({posted_et.strftime('%a %Y-%m-%d %H:%M ET')})")
        print(f"     @{r['author']}")
        print(f"     parser game_date: {r['game_date']}  ←  posted ET date: {posted_et.date()}")
        print(f"     verdict: {verdict}")
        text = (r["raw_text"] or "").replace("\n", " ")
        print(f"     text: {text[:200]}")
        print("-" * 110)

    print()
    print(f"Summary: ok={ok}  unverified={unverified}  BAD={bad}  total={len(rows)}")


if __name__ == "__main__":
    main()
