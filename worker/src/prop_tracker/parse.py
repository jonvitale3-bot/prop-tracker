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
from .prefilter import should_skip as prefilter_should_skip

log = logging.getLogger(__name__)

DEFAULT_BATCH_LIMIT = 200

ALLOWED_MARKETS = {
    # NBA player props
    "points", "rebounds", "assists", "threes", "steals", "blocks",
    "pra", "pa", "pr", "ra", "fantasy_points",
    "double_double", "triple_double",
    # MLB player props
    "hits", "total_bases", "home_runs", "runs", "rbis", "walks",
    "stolen_bases", "strikeouts_batter", "strikeouts_pitcher",
    "hits_runs_rbis",
    # Team markets (both sports)
    "spread", "total", "moneyline",
}
ALLOWED_SIDES = {"over", "under", "plus", "minus", "ml_home", "ml_away"}
ALLOWED_SPORTS = {"NBA", "MLB"}
ALLOWED_KINDS = {"player", "team"}


_SYSTEM_PROMPT = """You extract structured sports betting picks from social media posts.
The output is used to track public sentiment vs. actual outcomes. False
positives (extracting non-picks as picks) are MORE harmful than false
negatives (missing real picks). When in doubt, return no plays.

Output JSON ONLY (no prose, no markdown fences) in this exact shape:
{
  "plays": [
    {
      "sport": "NBA" or "MLB",
      "game_date": "YYYY-MM-DD",
      "subject": "Player Full Name or Team Name",
      "subject_kind": "player" or "team",
      "market": one of:
        NBA player: "points","rebounds","assists","threes","steals","blocks",
                    "pra","pa","pr","ra","fantasy_points","double_double","triple_double"
        MLB player: "hits","total_bases","home_runs","runs","rbis","walks",
                    "stolen_bases","strikeouts_batter","strikeouts_pitcher","hits_runs_rbis"
        team:       "spread","total","moneyline"
      "line": number or null,
      "side": "over"|"under"|"plus"|"minus"|"ml_home"|"ml_away",
      "conviction": 1|2|3|4|5,
      "market_explicit": true | false,
      "sportsbook": "DK"|"FD"|"MGM"|"Caesars"|"ESPN"|"BetRivers"|"Fanatics"|"Hardrock" or null,
      "odds_at_post": integer American odds (e.g. -110, +145) or null,
      "raw_quote": "<exact phrase from the post that constitutes the pick>"
    }
  ]
}

═══════════ WHAT IS NOT A PICK — REJECT THESE ═══════════

A post is NOT a pick (return {"plays": []}) if it falls into ANY of these:

1. PUBLIC-SENTIMENT REPORTING.
   The author is REPORTING ON betting, not betting. Reject phrases like:
   - "X% of bets on..." / "X% of money on..."
   - "the most backed team"
   - "getting the most action"
   - "the public is on..."
   - "consensus is..."
   - "biggest favorite price ever / since YYYY"
   Examples (REJECT):
     • "The Yankees (-170) are the most backed team on today's MLB slate, pulling in 90% of the money"
     • "63% of bets are on the Timberwolves to cover +9.5 tonight"
     • "Pirates are -330 with Skenes on the mound — biggest favorite price since 2005"

2. CASH RECAPS / PAST-TENSE RESULTS.
   The game already happened or is in progress. Reject if you see:
   - ✅, ❌, "cashed", "hit", "covered", "didn't cover"
   - "Never a doubt", "what a sweat", "no sweat"
   - Past-tense action verbs in proximity to a bet: "GIVES the lead",
     "WALKED IT OFF", "FINISHED with X points", "scored X in the first half",
     "came to play"
   - Live score updates ("X has 11 POINTS in the first 7 minutes")
   - Phrases like "good morning" / "tonight's results" / "today's recap"
   Examples (REJECT):
     • "✅ DONOVAN MITCHELL OVER 31.5 POINTS + REBOUNDS — Never a doubt!"
     • "COBY MAYO GIVES THE ORIOLES (+126) THE LEAD"
     • "Caris LeVert with 17 POINTS in the first half"
     • "7-3 in the MLB tonight! 40* LAD/COL Over 11.5 ✅ 40* Red Sox ML ✅..."

3. NEGATION / LOSING-SIDE COMMENTARY.
   The author is talking about a bet they LOST or telling you NOT to bet
   that specific side. Reject phrases like:
   - "was the wrong side", "should've been the other way"
   - "lost on", "took an L", "got cooked on", "down bad on", "stuck on"
   - ❌ next to a stated bet
   - "I'm 0-fer"
   These phrases describe a past loss, not a future pick.

   "Fade" and "burn me" are NOT automatic rejections. They often appear
   alongside a real pick the author is making:
     • "Fade me — taking Lakers ML"  → IS a pick (Lakers ML, conviction 2,
       the "fade me" is self-deprecating humor about the author's record)
     • "I'm a fade today, do the opposite" → REJECT (no specific pick named)

═══════════════════ WHAT IS A PICK ═══════════════════

A post IS a pick when the author is stating a bet they like / are placing
FOR an upcoming or in-progress event. Must explicitly contain:
- subject (player or team), AND
- market (or it must be obviously inferrable from the line), AND
- side (over/under, the team to bet, etc.).

If the post lists multiple picks (locks card, parlay legs, "tonight's plays"),
extract all of them. Each becomes its own play object in the array.

═══════════════════ EXTRACTION RULES ═══════════════════

- subject_kind="player" for player props; "team" for spread/total/moneyline.

- side semantics:
    * over/under for totals and player props
    * plus/minus for spread (plus=underdog +pts, minus=favorite -pts)
    * ml_home/ml_away for moneyline — but if you can't determine which
      team is home vs away from the post, REJECT the play rather than
      guessing. Do not default to ml_home.

- conviction: 1=mild lean, 2=lean, 3=clear pick, 4=strong, 5=hammer/lock/biggest of year.
  Recognise: 🔒, "lock", "hammer", "biggest of the year" → 5
              "love", "smash", "all over" → 4
              "like", "leaning", "lean" → 2
              Default conviction is 3 when stated as a pick with no qualifier.

- market_explicit:
    true  if the market name (points, hits, spread, etc.) is stated or
          unambiguously implied by the post's own wording
          ("Mitchell over 27.5 POINTS", "Padres u7 total", "Cubs ML").
    false if the market is being inferred from line magnitude alone
          (e.g., "Allen over 11.5" with no stat keyword — you guess
          rebounds because of the number). When market_explicit=false,
          **cap conviction at 2** regardless of other signals.

- MLB strikeouts disambiguation:
    * If the player is a known pitcher OR the line is > 5, use strikeouts_pitcher.
    * If the player is a known batter OR the line is < 3, use strikeouts_batter.
    * If ambiguous (line between 3 and 5 with no role hint), default to
      strikeouts_pitcher (more common bet type), and set market_explicit=false.

- "tonight", "today", "ce soir" → use the provided current date as game_date.

- Only NBA and MLB. Other sports (NHL, NFL, PGA, soccer, college) → ignore.
  If a post mixes NBA/MLB picks with non-NBA/MLB picks, extract only the
  NBA/MLB ones.

- If line is not stated (e.g. "Tatum over"), set line=null but keep the play.

- For MLB props, use the MLB market names (hits, total_bases, etc.).
  Do NOT squeeze MLB props into NBA combo buckets (pra/pa/pr/ra). The
  bucket "hits_runs_rbis" exists specifically for "H+R+RBIs" props.

- raw_quote: copy the exact phrase from the post that constitutes the
  pick. Do not paraphrase. If multiple picks, give each one its own quote.

Today's date is __TODAY_ISO__. Posts that say "tonight" or "today" refer
to this date.

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
    """Return True if the dict has the required fields with sane values.

    Side-effect: if market_explicit is False, clamps conviction to <= 2.
    Haiku doesn't always honor the prompt's cap rule, so enforce here.
    """
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
        # market_explicit defaults to True if missing (older callers / safety).
        explicit = p.get("market_explicit")
        if explicit is None:
            p["market_explicit"] = True
        elif explicit is False:
            # Cap conviction at 2 when market was inferred.
            p["conviction"] = min(conv, 2)
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
            play_id, mention_id, conviction, sportsbook, odds_at_post,
            raw_quote, market_explicit
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (play_id, mention_id) DO NOTHING
        """,
        (
            play_id, mention_id, int(p["conviction"]),
            p.get("sportsbook"), p.get("odds_at_post"), p.get("raw_quote"),
            bool(p.get("market_explicit", True)),
        ),
    )


def _mark_parsed(
    cur,
    mention_id: int,
    *,
    error: str | None = None,
    is_pick: bool | None = None,
    skip_reason: str | None = None,
) -> None:
    """Stamp the mention as processed.

    Outcomes recorded:
      * is_pick=True,  skip_reason=None         → Haiku extracted at least one play
      * is_pick=False, skip_reason='no_pick'    → Haiku returned []
      * is_pick=False, skip_reason='wrong_sport'→ prefilter dropped before Haiku
      * is_pick=NULL,  skip_reason='parse_error', error=...  → exception path
    """
    cur.execute(
        """
        UPDATE mentions
        SET parsed_at  = now(),
            parse_error = %s,
            is_pick     = %s,
            skip_reason = %s
        WHERE id = %s
        """,
        (error, is_pick, skip_reason, mention_id),
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
    prefiltered = 0

    for row in rows:
        mid = row["id"]
        text = row["raw_text"]
        posted_at = row["posted_at"]

        # 1) Prefilter — drop obvious non-NBA/MLB before spending a Haiku
        #    call on it. The mention row is still updated so the audit
        #    trail (is_pick=false, skip_reason=...) is preserved.
        skip, reason = prefilter_should_skip(text)
        if skip:
            prefiltered += 1
            try:
                with connect() as conn, conn.cursor() as cur:
                    _mark_parsed(cur, mid, is_pick=False, skip_reason=reason)
                    conn.commit()
                log.info("parse: mention %d prefiltered (%s)", mid, reason)
            except Exception:
                log.exception("parse: failed to mark prefiltered mention %d", mid)
            continue

        # 2) Send to Haiku.
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
                if kept > 0:
                    _mark_parsed(cur, mid, is_pick=True)
                else:
                    _mark_parsed(cur, mid, is_pick=False, skip_reason="no_pick")
                conn.commit()
            total_plays += kept
            log.info("parse: mention %d -> %d play(s) kept (raw: %d)", mid, kept, len(plays_in))
        except (APIError, json.JSONDecodeError, Exception) as e:  # noqa: BLE001
            failed += 1
            err = f"{type(e).__name__}: {e}"[:500]
            log.warning("parse: mention %d failed: %s", mid, err)
            try:
                with connect() as conn, conn.cursor() as cur:
                    _mark_parsed(cur, mid, error=err, skip_reason="parse_error")
                    conn.commit()
            except Exception:
                log.exception("parse: also failed to record parse_error for mention %d", mid)

    log.info(
        "parse: done. mentions=%d prefiltered=%d plays_extracted=%d failed=%d",
        len(rows), prefiltered, total_plays, failed,
    )

    # Per-account yield summary for this batch — useful for spotting
    # accounts that produce mentions but no parseable picks.
    _log_per_account_yield([row["id"] for row in rows])


def _log_per_account_yield(mention_ids: list[int]) -> None:
    """Group the just-parsed mentions by author and log mentions vs plays."""
    if not mention_ids:
        return
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.author, m.author_tier,
                       count(*) AS mentions,
                       count(DISTINCT pm.play_id) AS plays
                FROM mentions m
                LEFT JOIN play_mentions pm ON pm.mention_id = m.id
                WHERE m.id = ANY(%s)
                GROUP BY m.author, m.author_tier
                ORDER BY mentions DESC
                """,
                (mention_ids,),
            )
            rows = cur.fetchall()
    except Exception:
        log.exception("parse: failed to compute per-account yield")
        return
    if not rows:
        return
    log.info("parse: per-account yield this batch:")
    for r in rows:
        rate = (r["plays"] / r["mentions"] * 100) if r["mentions"] else 0
        author = r["author"] or "<missing>"
        tier = r["author_tier"] if r["author_tier"] is not None else "?"
        log.info(
            "  @%-22s [tier=%s]  mentions=%d  plays=%d  yield=%.1f%%",
            author, tier, r["mentions"], r["plays"], rate,
        )
