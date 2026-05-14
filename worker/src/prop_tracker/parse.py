"""Claude Haiku parser: raw mentions -> structured plays.

Reads `mentions` rows where parsed_at IS NULL, asks Claude to extract
zero-or-more betting picks per post, then upserts into `plays` and links
via `play_mentions`. After each upsert we recompute `public_pct` for every
side of the affected bet so totals always sum to 100%.

Conservative extraction: only emit a play when the post clearly states a
pick (subject + market + side). Hot takes without an explicit bet are
ignored. Plays with `line=NULL` are kept (sentiment value), but the
grader will mark them ungradeable.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

import anthropic
from anthropic import APIError

from .config import load_settings, require_anthropic
from .db import connect

log = logging.getLogger(__name__)

DEFAULT_BATCH_LIMIT = 200

ALLOWED_MARKETS = {
    "points", "rebounds", "assists", "threes", "steals", "blocks",
    "pra", "pa", "pr", "ra", "fantasy_points",
    "double_double", "triple_double",
    "spread", "total", "moneyline",
}
ALLOWED_SIDES = {"over", "under", "plus", "minus", "ml_home", "ml_away"}
ALLOWED_SPORTS = {"NBA", "MLB"}
ALLOWED_KINDS = {"player", "team"}


_SYSTEM_PROMPT = """You extract structured sports betting picks from social media posts.

Output JSON ONLY in this exact shape (no prose, no markdown fences):
{
  "plays": [
    {
      "sport": "NBA" or "MLB",
      "game_date": "YYYY-MM-DD",
      "subject": "Player Full Name or Team Name",
      "subject_kind": "player" or "team",
      "market": one of: "points","rebounds","assists","threes","steals","blocks","pra","pa","pr","ra","fantasy_points","double_double","triple_double","spread","total","moneyline",
      "line": number or null,
      "side": "over"|"under"|"plus"|"minus"|"ml_home"|"ml_away",
      "conviction": 1|2|3|4|5,
      "sportsbook": "DK"|"FD"|"MGM"|"Caesars"|"ESPN"|"BetRivers"|"Fanatics"|"Hardrock" or null,
      "odds_at_post": integer American odds (e.g. -110, +145) or null,
      "raw_quote": "<exact phrase from the post that constitutes the pick>"
    }
  ]
}

Rules:
- BE CONSERVATIVE. Only emit a play when the post explicitly states a pick
  (subject + market + side). Vague hot takes without an explicit bet -> no play.
- A single post may contain multiple bets; extract all of them.
- If the post has no bets, return exactly: {"plays": []}
- "tonight", "today", "ce soir" -> use the provided current date as game_date.
- conviction: 1=mild lean, 2=lean, 3=clear pick, 4=strong, 5=hammer/lock/biggest of year.
- subject_kind="player" for player props; "team" for spread/total/moneyline.
- side semantics:
    * over/under for totals and player props
    * plus/minus for spread (plus=betting the underdog +pts, minus=betting the favorite -pts)
    * ml_home/ml_away for moneyline (if unclear which side, pick ml_home)
- Only NBA and MLB. Ignore picks for other sports.
- If line is not stated (e.g. "Tatum over"), set line=null but keep the play.
- raw_quote: copy the exact phrase from the post (do not paraphrase).

Today's date is __TODAY_ISO__. Posts that say "tonight" or "today" refer to this date.

Return JSON only.
"""


def _build_user_msg(text: str, posted_at_iso: str) -> str:
    return (
        f"<post posted_at=\"{posted_at_iso}\">\n"
        f"{text}\n"
        f"</post>\n\n"
        f"Extract plays as JSON."
    )


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """Claude sometimes wraps JSON in prose/fences despite instructions. Be
    tolerant: try direct parse, then fall back to the first {...} block."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_OBJECT_RE.search(text)
        if not m:
            raise
        return json.loads(m.group(0))


def _valid_play(p: dict[str, Any]) -> bool:
    """Return True if the dict has the required fields with sane values."""
    try:
        if p.get("sport") not in ALLOWED_SPORTS:
            return False
        if p.get("subject_kind") not in ALLOWED_KINDS:
            return False
        if p.get("market") not in ALLOWED_MARKETS:
            return False
        if p.get("side") not in ALLOWED_SIDES:
            return False
        if not isinstance(p.get("subject"), str) or not p["subject"].strip():
            return False
        conv = p.get("conviction")
        if not (isinstance(conv, int) and 1 <= conv <= 5):
            return False
        # game_date must be parseable
        gd = p.get("game_date")
        if not isinstance(gd, str):
            return False
        datetime.strptime(gd, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    return True


def _call_claude(
    client: anthropic.Anthropic, model: str, text: str, posted_at: datetime
) -> dict[str, Any]:
    today_iso = date.today().isoformat()
    # Use str.replace not .format() because the prompt contains literal {} from
    # the JSON example and Python's .format would mis-parse them.
    system = _SYSTEM_PROMPT.replace("__TODAY_ISO__", today_iso)
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": _build_user_msg(text, posted_at.isoformat())}],
    )
    # content is a list of content blocks; the first text block is what we want.
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    body = "\n".join(parts).strip()
    return _extract_json(body)


def _upsert_play(cur, p: dict[str, Any]) -> int:
    """Find-or-insert a plays row and update its rolling stats. Returns id."""
    cur.execute(
        """
        SELECT id, mention_count, COALESCE(avg_conviction, 0) AS avg_conviction
        FROM plays
        WHERE sport = %s AND game_date = %s AND subject = %s AND market = %s
          AND COALESCE(line, -99999) = COALESCE(%s, -99999)
          AND side = %s
        """,
        (p["sport"], p["game_date"], p["subject"], p["market"], p.get("line"), p["side"]),
    )
    row = cur.fetchone()
    new_conv = float(p["conviction"])
    if row is not None:
        n = row["mention_count"] + 1
        new_avg = (float(row["avg_conviction"]) * row["mention_count"] + new_conv) / n
        cur.execute(
            """
            UPDATE plays
            SET mention_count = %s,
                avg_conviction = %s,
                last_updated_at = now()
            WHERE id = %s
            """,
            (n, new_avg, row["id"]),
        )
        return row["id"]

    cur.execute(
        """
        INSERT INTO plays (
            sport, game_date, subject, subject_kind, market, line, side,
            mention_count, avg_conviction
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s)
        RETURNING id
        """,
        (
            p["sport"], p["game_date"], p["subject"], p["subject_kind"],
            p["market"], p.get("line"), p["side"], new_conv,
        ),
    )
    return cur.fetchone()["id"]


def _recompute_public_pct(cur, p: dict[str, Any]) -> None:
    """Recompute public_pct for every side of the (sport, game_date, subject,
    market, line) tuple so they sum to 100%."""
    cur.execute(
        """
        UPDATE plays
        SET public_pct = ROUND(
            mention_count * 100.0 / NULLIF(sums.total, 0), 2
        )
        FROM (
            SELECT sum(mention_count) AS total
            FROM plays
            WHERE sport = %s AND game_date = %s AND subject = %s AND market = %s
              AND COALESCE(line, -99999) = COALESCE(%s, -99999)
        ) sums
        WHERE plays.sport = %s AND plays.game_date = %s AND plays.subject = %s
          AND plays.market = %s
          AND COALESCE(plays.line, -99999) = COALESCE(%s, -99999)
        """,
        (
            p["sport"], p["game_date"], p["subject"], p["market"], p.get("line"),
            p["sport"], p["game_date"], p["subject"], p["market"], p.get("line"),
        ),
    )


def _link_mention(cur, play_id: int, mention_id: int, p: dict[str, Any]) -> None:
    cur.execute(
        """
        INSERT INTO play_mentions (
            play_id, mention_id, conviction, sportsbook, odds_at_post, raw_quote
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (play_id, mention_id) DO NOTHING
        """,
        (
            play_id, mention_id, int(p["conviction"]),
            p.get("sportsbook"), p.get("odds_at_post"), p.get("raw_quote"),
        ),
    )


def _mark_parsed(cur, mention_id: int, error: str | None) -> None:
    cur.execute(
        "UPDATE mentions SET parsed_at = now(), parse_error = %s WHERE id = %s",
        (error, mention_id),
    )


def run(limit: int = DEFAULT_BATCH_LIMIT) -> None:
    settings = load_settings()
    api_key = require_anthropic(settings)
    client = anthropic.Anthropic(api_key=api_key)
    model = settings.anthropic_model

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, raw_text, posted_at
            FROM mentions
            WHERE parsed_at IS NULL
            ORDER BY posted_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

    log.info("parse: %d unparsed mention(s) to process (model=%s)", len(rows), model)
    total_plays = 0
    failed = 0

    for row in rows:
        mid = row["id"]
        text = row["raw_text"]
        posted_at = row["posted_at"]
        try:
            result = _call_claude(client, model, text, posted_at)
            plays_in = result.get("plays") or []
            kept = 0
            with connect() as conn, conn.cursor() as cur:
                for p in plays_in:
                    if not _valid_play(p):
                        log.debug("parse: dropping invalid play from mention %d: %r", mid, p)
                        continue
                    play_id = _upsert_play(cur, p)
                    _link_mention(cur, play_id, mid, p)
                    _recompute_public_pct(cur, p)
                    kept += 1
                _mark_parsed(cur, mid, error=None)
                conn.commit()
            total_plays += kept
            log.info("parse: mention %d -> %d play(s) kept (raw: %d)", mid, kept, len(plays_in))
        except (APIError, json.JSONDecodeError, Exception) as e:  # noqa: BLE001
            failed += 1
            err = f"{type(e).__name__}: {e}"[:500]
            log.warning("parse: mention %d failed: %s", mid, err)
            try:
                with connect() as conn, conn.cursor() as cur:
                    _mark_parsed(cur, mid, error=err)
                    conn.commit()
            except Exception:
                log.exception("parse: also failed to record parse_error for mention %d", mid)

    log.info("parse: done. mentions=%d plays_extracted=%d failed=%d",
             len(rows), total_plays, failed)
