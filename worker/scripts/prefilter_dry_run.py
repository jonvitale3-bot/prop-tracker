"""One-off dry-run for the sport prefilter.

Reads the last 200 handle_scrape mentions, applies the proposed prefilter,
and prints per-author drop counts. NOT wired into the ingest loop —
this is a sanity-check tool only. Delete after Phase 1 polish is done.

Usage (from repo root):
    cd worker && uv run python scripts/prefilter_dry_run.py
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

# Bootstrap dotenv from repo root so the script works regardless of CWD.
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=True)

import psycopg
from psycopg.rows import dict_row

DSN = os.environ["DATABASE_URL"]


# ── Prefilter rules ───────────────────────────────────────────────
#
# Decision logic:
#   1. If text matches an OTHER_SPORT marker AND does NOT match any
#      NBA or MLB marker → SKIP (counted as filtered).
#   2. Otherwise → KEEP and pass to the parser.
#
# Generic betting terms (spread / moneyline / total / over / under /
# parlay / lock / unit / +EV / -EV) do NOT count as NBA/MLB markers —
# they appear across all sports. Only league names, team names, player
# surnames, and sport-specific stat markets override the OTHER_SPORT
# match.

# Other sports we explicitly don't cover. WNBA is in here per Phase 1
# decision (different schema; will revisit later if useful).
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

# NBA-specific markers: league name, NBA-only markets, all 30 team
# names + common short forms. Player surnames are NOT in here — too
# many overlap with MLB (e.g. "Jones"). League + team + market is
# sufficient signal in practice.
NBA_MARKERS = re.compile(
    r"\b("
    r"nba|"
    # Sport-specific markets
    r"rebounds?|assists?|pra|points\+rebounds|"
    r"3[- ]?pointers?|threes\s+made|3pm|blocks?|steals?|"
    # All 30 teams (full names + short forms)
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
    # Sport-specific markets
    r"home\s+runs?|homers?|hrs?|rbis?|total\s+bases|"
    r"strikeouts?|hits?|stolen\s+bases?|walks?|earned\s+runs?|"
    r"innings\s+pitched|outs\s+recorded|"
    # All 30 teams (full names + short forms)
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
    """Returns (skip, reason). reason is a short tag for logging."""
    if not text:
        return False, None
    other = OTHER_SPORT.search(text)
    if not other:
        return False, None  # no foreign-sport signal; let parser decide
    if NBA_MARKERS.search(text) or MLB_MARKERS.search(text):
        return False, None  # mixed-sport tweet; NBA/MLB content present
    return True, other.group(1).lower()


def main() -> None:
    with psycopg.connect(DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, author, raw_text
            FROM mentions
            WHERE source_method = 'handle_scrape'
            ORDER BY id DESC
            LIMIT 200
            """
        )
        rows = cur.fetchall()

    by_author_total: Counter[str] = Counter()
    by_author_skipped: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    sample_skips: dict[str, list[tuple[str, str]]] = {}

    for r in rows:
        author = r["author"] or "<none>"
        by_author_total[author] += 1
        skip, reason = should_skip(r["raw_text"])
        if skip:
            by_author_skipped[author] += 1
            skip_reasons[reason] += 1
            sample_skips.setdefault(author, []).append((reason, r["raw_text"][:140]))

    total = sum(by_author_total.values())
    total_skipped = sum(by_author_skipped.values())

    print(f"Last {total} handle_scrape mentions")
    print(f"Total skipped: {total_skipped} ({100*total_skipped/total:.1f}%)\n")

    print("Per-author breakdown:")
    print(f"  {'handle':<22} {'mentions':>9} {'skipped':>8} {'rate':>6}")
    for author, n in by_author_total.most_common():
        s = by_author_skipped.get(author, 0)
        rate = f"{100*s/n:.0f}%" if n else "-"
        print(f"  {author:<22} {n:>9} {s:>8} {rate:>6}")

    print("\nTop skip triggers:")
    for reason, n in skip_reasons.most_common(10):
        print(f"  {reason:<25} {n}")

    print("\nSample skipped tweets (up to 2 per author):")
    for author, samples in sample_skips.items():
        for reason, text in samples[:2]:
            print(f"  @{author} [{reason}] {text}")


if __name__ == "__main__":
    main()
