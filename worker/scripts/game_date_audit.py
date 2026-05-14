"""Diagnostic: audit parser-assigned game_date vs reality.

Pulls 20 random plays from the last 7 days (excluding legacy-quarantined
rows, so we only measure the post-fix parser), joins to the source
mention, and for each one:

  - shows tweet posted_at (UTC + ET), parser game_date, and raw_text
  - hits ESPN scoreboard for that sport on game_date to see if games exist
  - if subject_kind='team', checks whether the team appears in the day's games
  - if subject_kind='player', best-effort: checks whether any team appearing
    in the day's games is also mentioned in raw_text (partial verification)
  - classifies the verdict into OK / BAD / SUSPECT / UNVERIFIED
  - detects day-reference language in raw_text ("tonight", "tomorrow",
    weekday names) and the post's ET hour bucket so the summary can
    report edge-case coverage

Output is the per-play table + an accuracy breakdown + edge-case
coverage. No DB writes. Delete after the game-date issue is closed.

Usage (from repo root):
    cd worker && .venv/bin/python scripts/game_date_audit.py
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=True)

import psycopg
from psycopg.rows import dict_row

DSN = os.environ["DATABASE_URL"]
ET = ZoneInfo("America/New_York")

ESPN_URLS = {
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={d}",
    "MLB": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={d}",
}

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_RE = re.compile(r"\b(" + "|".join(WEEKDAYS) + r")\b", re.IGNORECASE)
TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)
TONIGHT_RE = re.compile(r"\b(tonight|today)\b", re.IGNORECASE)


def fetch_espn(sport: str, game_date: str) -> dict:
    d = game_date.replace("-", "")
    url = ESPN_URLS[sport].format(d=d)
    req = urllib.request.Request(url, headers={"User-Agent": "prop-tracker-audit"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def teams_on_date(payload: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for ev in payload.get("events", []):
        for comp in ev.get("competitions", []):
            for c in comp.get("competitors", []):
                team = c.get("team", {})
                out.append((team.get("displayName", ""), team.get("shortDisplayName", "")))
    return out


def detect_day_refs(text: str) -> list[str]:
    """Return list of detected day-reference tags in the tweet."""
    refs: list[str] = []
    if TOMORROW_RE.search(text or ""):
        refs.append("tomorrow")
    if TONIGHT_RE.search(text or ""):
        refs.append("today/tonight")
    wm = WEEKDAY_RE.search(text or "")
    if wm:
        refs.append(f"weekday:{wm.group(1).lower()}")
    return refs


def hour_bucket(hour_et: int) -> str:
    if 0 <= hour_et <= 4:
        return "00–04 ET (late-night)"
    if 5 <= hour_et < 12:
        return "05–11 ET (morning)"
    if 12 <= hour_et < 17:
        return "12–16 ET (afternoon)"
    if 17 <= hour_et < 20:
        return "17–19 ET (early eve)"
    return "20–23 ET (evening)"


def verify(sport: str, subject: str, subject_kind: str, raw_text: str, game_date: str) -> str:
    try:
        payload = fetch_espn(sport, game_date)
    except Exception as e:
        return f"espn-error: {e}"
    teams = teams_on_date(payload)
    if not teams:
        return "BAD (no games that day)"
    if subject_kind == "team":
        subj_l = subject.lower()
        for full, short in teams:
            if full and (full.lower() in subj_l or subj_l in full.lower()):
                return f"OK (team in slate: {full})"
            if short and (short.lower() in subj_l or subj_l in short.lower()):
                return f"OK (team in slate: {short})"
        return f"BAD (team not on slate; {len(teams)//2} games scheduled)"
    matches = []
    raw_l = (raw_text or "").lower()
    for full, short in teams:
        if full and full.lower() in raw_l:
            matches.append(full)
        elif short and short.lower() in raw_l:
            matches.append(short)
    if matches:
        return f"OK (player; raw_text names slate team: {sorted(set(matches))[0]})"
    return f"UNVERIFIED (player; no slate team named in tweet; {len(teams)//2} games scheduled)"


def expected_game_date(posted_et_date, posted_et_hour: int, day_refs: list[str]) -> str | None:
    """Compute the game_date the parser SHOULD have produced for this tweet,
    based on the day-reference rules. Returns None if too ambiguous to assert.
    """
    # "tomorrow" → anchor + 1
    if "tomorrow" in day_refs:
        return (posted_et_date + timedelta(days=1)).isoformat()
    # weekday reference → next occurrence (or same if it matches anchor day)
    for ref in day_refs:
        if ref.startswith("weekday:"):
            wd_name = ref.split(":", 1)[1]
            wd_idx = WEEKDAYS.index(wd_name)
            today_idx = posted_et_date.weekday()
            delta = (wd_idx - today_idx) % 7
            return (posted_et_date + timedelta(days=delta)).isoformat()
    # No explicit reference or "today/tonight" → anchor day.
    return posted_et_date.isoformat()


def main() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with psycopg.connect(DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id AS play_id, p.sport, p.game_date::text AS game_date,
                   p.subject, p.subject_kind, p.market, p.side, p.line,
                   p.legacy_game_date,
                   m.id AS mention_id, m.author, m.posted_at, m.raw_text
            FROM plays p
            JOIN play_mentions pm ON pm.play_id = p.id
            JOIN mentions m ON m.id = pm.mention_id
            WHERE m.source_method = 'handle_scrape'
              AND m.posted_at >= %s::timestamptz
              AND p.legacy_game_date = FALSE   -- post-fix plays only
            ORDER BY random()
            LIMIT 20
            """,
            (cutoff,),
        )
        rows = cur.fetchall()

    if not rows:
        print("No NON-LEGACY plays found in the last 7 days. "
              "Wait for the next Railway parse cycle and re-run.")
        return

    print(f"Sampled {len(rows)} non-legacy plays from the last 7 days.\n")
    print("=" * 110)

    verdict_counts: Counter[str] = Counter()  # OK / BAD / SUSPECT / UNVERIFIED
    hour_buckets: Counter[str] = Counter()
    ref_buckets: Counter[str] = Counter()
    suspect_cases: list[dict[str, Any]] = []

    for i, r in enumerate(rows, 1):
        posted_utc = r["posted_at"]
        posted_et = posted_utc.astimezone(ET)
        day_refs = detect_day_refs(r["raw_text"] or "")
        expected = expected_game_date(posted_et.date(), posted_et.hour, day_refs)

        verdict = verify(
            r["sport"], r["subject"], r["subject_kind"],
            r["raw_text"] or "", r["game_date"],
        )

        # Classify into OK / BAD / SUSPECT / UNVERIFIED.
        # SUSPECT = ESPN says a game existed on the assigned date AND the
        # play's subject is present there, BUT the date doesn't match the
        # day-reference rule (e.g. no "tomorrow" in text, but game_date is
        # post day + 1). Most likely the parser drifted +1 day again.
        if verdict.startswith("BAD"):
            klass = "BAD"
        elif verdict.startswith("UNVERIFIED"):
            klass = "UNVERIFIED"
        elif verdict.startswith("OK"):
            if expected is not None and r["game_date"] != expected:
                klass = "SUSPECT"
                suspect_cases.append({
                    "play_id": r["play_id"],
                    "got": r["game_date"],
                    "expected": expected,
                    "refs": day_refs,
                })
            else:
                klass = "OK"
        else:
            klass = "ESPN-ERROR"

        verdict_counts[klass] += 1
        hour_buckets[hour_bucket(posted_et.hour)] += 1
        if day_refs:
            for ref in day_refs:
                ref_buckets[ref] += 1
        else:
            ref_buckets["(none)"] += 1

        play_str = f"{r['subject']} {r['market']} {r['side']}"
        if r["line"] is not None:
            play_str += f" {r['line']}"
        print(f"[{i:>2}] play {r['play_id']}: {r['sport']} {play_str}   [{klass}]")
        print(f"     posted: {posted_utc.isoformat()} UTC  "
              f"({posted_et.strftime('%a %Y-%m-%d %H:%M ET')})  bucket={hour_bucket(posted_et.hour)}")
        print(f"     @{r['author']}   day_refs={day_refs or '(none)'}")
        print(f"     parser game_date={r['game_date']}   "
              f"expected={expected}   posted ET date={posted_et.date()}")
        print(f"     espn verdict: {verdict}")
        text = (r["raw_text"] or "").replace("\n", " ")
        print(f"     text: {text[:200]}")
        print("-" * 110)

    total = sum(verdict_counts.values())
    ok = verdict_counts.get("OK", 0)
    bad = verdict_counts.get("BAD", 0)
    suspect = verdict_counts.get("SUSPECT", 0)
    unverified = verdict_counts.get("UNVERIFIED", 0)
    espn_err = verdict_counts.get("ESPN-ERROR", 0)

    # Accuracy denominator excludes UNVERIFIED + ESPN-ERROR (we can't judge them).
    judgeable = ok + bad + suspect
    acc = (ok / judgeable * 100) if judgeable else 0.0

    print()
    print("════════════════════ ACCURACY BREAKDOWN ════════════════════")
    print(f"  Plays sampled:        {total}")
    print(f"  OK:                   {ok}")
    print(f"  BAD:                  {bad}   (no game scheduled / team not on slate)")
    print(f"  SUSPECT:              {suspect}   (game exists but date != day-rule expected)")
    print(f"  UNVERIFIED (player):  {unverified}")
    if espn_err:
        print(f"  ESPN-ERROR:           {espn_err}")
    print(f"  Judgeable (OK+BAD+SUSPECT): {judgeable}")
    print(f"  Accuracy on judgeable: {acc:.1f}%   (target: ≥90%)")

    if suspect_cases:
        print()
        print("SUSPECT cases (game existed but date != expected):")
        for s in suspect_cases:
            print(f"  play {s['play_id']}: got={s['got']}  expected={s['expected']}  refs={s['refs']}")

    print()
    print("════════════════════ EDGE-CASE COVERAGE ════════════════════")
    print("ET hour buckets in this sample:")
    for b in ["00–04 ET (late-night)", "05–11 ET (morning)", "12–16 ET (afternoon)",
              "17–19 ET (early eve)", "20–23 ET (evening)"]:
        n = hour_buckets.get(b, 0)
        flag = " ← MISSING" if n == 0 and b in ("00–04 ET (late-night)", "20–23 ET (evening)") else ""
        print(f"  {b:<28} {n}{flag}")

    print()
    print("Day-reference language in this sample:")
    if not ref_buckets:
        print("  (nothing detected)")
    else:
        for ref, n in ref_buckets.most_common():
            print(f"  {ref:<28} {n}")
    print()
    print("Required edge cases (per spec):")
    checks = [
        ("Posted before 5pm ET",
         hour_buckets["05–11 ET (morning)"] + hour_buckets["12–16 ET (afternoon)"] > 0),
        ("Posted 20:00–23:00 ET (previously broken)",
         hour_buckets["20–23 ET (evening)"] > 0),
        ("Posted 00:00–04:00 ET (late-night exception)",
         hour_buckets["00–04 ET (late-night)"] > 0),
        ("'tomorrow' or weekday-name reference",
         ref_buckets["tomorrow"] + sum(n for k, n in ref_buckets.items() if k.startswith("weekday:")) > 0),
        ("No explicit day reference",
         ref_buckets.get("(none)", 0) > 0),
    ]
    for label, ok_flag in checks:
        marker = "✓" if ok_flag else "✗ MISSING"
        print(f"  {marker}  {label}")


if __name__ == "__main__":
    main()
