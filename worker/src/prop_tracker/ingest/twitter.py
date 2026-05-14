"""Twitter ingester (handle-based).

Reads a curated YAML list of accounts at worker/config/twitter_accounts.yaml
and scrapes the last 24h of *original* tweets (no retweets, no replies)
from each handle via the kaitoeasyapi pay-per-result actor.

Each tweet is stored in `mentions` verbatim with:
    source         = 'twitter'
    source_id      = tweet id (Apify's id field)
    author         = handle (without the @)
    posted_at      = tweet's createdAt
    raw_text       = tweet text
    engagement     = likes + retweets + replies
    source_method  = 'handle_scrape'
    author_tier    = 1/2/3/4 from YAML

The parser runs separately on rows where parsed_at IS NULL.

Per-handle logging includes: tweets returned, retweets filtered, replies
filtered, original tweets inserted, duplicates skipped.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from ..apify import run_actor_sync
from ..config import load_settings, require_apify
from ..db import connect
from .common import Mention, coerce_int, coerce_str

log = logging.getLogger(__name__)

# kaitoeasyapi tweets out per *searchTerm*; we set this generously per handle.
# Sharp accounts post a few times a day, public/brand accounts can post 50+.
MAX_ITEMS_PER_HANDLE = int(os.environ.get("TWITTER_MAX_ITEMS_PER_HANDLE", "50"))

# Twitter lookback window in hours. Twitter's `since:` operator is
# date-precision only, so we compute `since:DATE` from (now - lookback)
# and additionally drop any tweet older than the window post-fetch.
# 12h keeps cost down while staying resilient to short outages.
LOOKBACK_HOURS = int(os.environ.get("TWITTER_LOOKBACK_HOURS", "12"))

_ACCOUNTS_PATH = Path(__file__).resolve().parents[3] / "config" / "twitter_accounts.yaml"


# ── account config ────────────────────────────────────────────

def _load_accounts() -> list[dict[str, Any]]:
    """Load accounts from YAML, returning only ones flagged active: true.

    `active` defaults to True if the field is absent (back-compat with the
    v2 YAML that had no flag). Set `active: false` to keep an entry on
    disk for audit / future re-activation without scraping it each cycle.
    """
    if not _ACCOUNTS_PATH.exists():
        raise RuntimeError(f"Twitter accounts file not found: {_ACCOUNTS_PATH}")
    with _ACCOUNTS_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    accounts = data.get("accounts") or []
    out: list[dict[str, Any]] = []
    skipped = 0
    for a in accounts:
        h = (a.get("handle") or "").strip().lstrip("@")
        if not h:
            continue
        if a.get("active", True) is False:
            skipped += 1
            continue
        out.append({
            "handle": h,
            "tier": int(a.get("tier") or 0) or None,
            "sport_focus": a.get("sport_focus"),
            "notes": a.get("notes"),
            "produces_inline_picks": bool(a.get("produces_inline_picks", True)),
        })
    if skipped:
        log.info("Twitter accounts: loaded %d active, %d skipped (active: false)",
                 len(out), skipped)
    return out


# ── Apify input ───────────────────────────────────────────────

def _build_search_terms(handles: list[str], since: date) -> list[str]:
    iso = since.isoformat()
    return [f"from:{h} since:{iso}" for h in handles]


def _drop_old(items: list[dict[str, Any]], cutoff: datetime) -> tuple[list[dict[str, Any]], int]:
    """Filter items posted before `cutoff`. Returns (kept, dropped_count).
    Apify charges per result returned, but downstream parser cost is per
    *new* mention inserted — this filter drops Anthropic cost for items
    inside the date window but outside the hours window."""
    kept = []
    dropped = 0
    for it in items:
        ts = it.get('createdAt') or it.get('created_at')
        try:
            posted = _parse_dt(ts)
            if posted >= cutoff:
                kept.append(it)
            else:
                dropped += 1
        except Exception:
            kept.append(it)  # if unparseable, keep it (let the normalizer decide)
    return kept, dropped


def _build_input(handles: list[str], since: date) -> dict[str, Any]:
    return {
        "searchTerms": _build_search_terms(handles, since),
        "maxItems": MAX_ITEMS_PER_HANDLE * len(handles),
        "queryType": "Latest",
        "lang": "en",
        # The actor supports filter:replies / filter:nativeretweets, but we
        # filter post-hoc too because some retweets/replies slip through
        # depending on Twitter's search behavior.
    }


# ── output classification ─────────────────────────────────────

def _is_retweet(item: dict[str, Any]) -> bool:
    if item.get("retweeted_tweet"):
        return True
    text = item.get("text") or ""
    return text.startswith("RT @")


def _is_reply(item: dict[str, Any]) -> bool:
    if item.get("isReply") is True:
        return True
    if item.get("inReplyToId"):
        return True
    return False


def _author_handle(item: dict[str, Any]) -> str | None:
    a = item.get("author") or {}
    return coerce_str(a.get("userName") or a.get("username") or a.get("screen_name"))


def _parse_dt(v: Any) -> datetime:
    if v is None:
        return datetime.now(timezone.utc)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    s = str(v)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            return datetime.now(timezone.utc)


def _to_mention(item: dict[str, Any], handle: str, tier: int | None) -> Mention | None:
    source_id = coerce_str(item.get("id") or item.get("tweetId"))
    text = coerce_str(item.get("text") or item.get("full_text") or item.get("fullText"))
    if not source_id or not text:
        return None

    likes = coerce_int(item.get("likeCount") or item.get("favorite_count"))
    rts = coerce_int(item.get("retweetCount") or item.get("retweet_count"))
    replies = coerce_int(item.get("replyCount") or item.get("reply_count"))

    return Mention(
        source="twitter",
        source_id=source_id,
        author=handle,
        url=coerce_str(item.get("url") or item.get("twitterUrl"))
            or f"https://x.com/{handle}/status/{source_id}",
        posted_at=_parse_dt(item.get("createdAt") or item.get("created_at")),
        raw_text=text,
        engagement_score=likes + rts + replies,
        source_method="handle_scrape",
        author_tier=tier,
    )


# ── DB helpers (per-handle dedup count) ───────────────────────

def _existing_source_ids(source_ids: list[str]) -> set[str]:
    if not source_ids:
        return set()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source_id FROM mentions WHERE source = 'twitter' AND source_id = ANY(%s)",
            (source_ids,),
        )
        return {row["source_id"] for row in cur.fetchall()}


def _insert_mentions(mentions: list[Mention]) -> int:
    """Insert mentions one-by-one so the caller can count exact inserts
    (different from upsert_mentions which is bulk-only-batched).
    Returns count inserted."""
    if not mentions:
        return 0
    sql = """
        INSERT INTO mentions (
            source, source_id, author, url, posted_at, raw_text,
            engagement_score, source_method, author_tier
        ) VALUES (
            %(source)s, %(source_id)s, %(author)s, %(url)s,
            %(posted_at)s, %(raw_text)s, %(engagement_score)s,
            %(source_method)s, %(author_tier)s
        )
        ON CONFLICT (source, source_id) DO NOTHING
    """
    n = 0
    with connect() as conn, conn.cursor() as cur:
        for m in mentions:
            cur.execute(sql, {
                "source": m.source, "source_id": m.source_id,
                "author": m.author, "url": m.url,
                "posted_at": m.posted_at, "raw_text": m.raw_text,
                "engagement_score": m.engagement_score,
                "source_method": m.source_method, "author_tier": m.author_tier,
            })
            n += cur.rowcount
        conn.commit()
    return n


# ── main entry ────────────────────────────────────────────────

def run() -> None:
    settings = load_settings()
    token = require_apify(settings)
    accounts = _load_accounts()
    if not accounts:
        log.warning("Twitter ingest: no accounts in YAML; nothing to do")
        return

    handles = [a["handle"] for a in accounts]
    tier_by_handle = {a["handle"].lower(): a["tier"] for a in accounts}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    since = cutoff.date()  # date-precision for Twitter's `since:` operator
    log.info(
        "Twitter ingest (handle_scrape): %d accounts, lookback=%dh (since=%s), "
        "max_per_handle=%d, actor=%s",
        len(handles), LOOKBACK_HOURS, since.isoformat(),
        MAX_ITEMS_PER_HANDLE, settings.apify_twitter_actor,
    )

    items = run_actor_sync(
        actor_id=settings.apify_twitter_actor,
        token=token,
        actor_input=_build_input(handles, since),
    )

    # Hours-precision filter: drop anything older than the cutoff.
    items, old_dropped = _drop_old(items, cutoff)
    if old_dropped:
        log.info("Twitter ingest: dropped %d items older than %dh cutoff",
                 old_dropped, LOOKBACK_HOURS)

    # Bucket by author (lowercased so case mismatches don't drop tweets).
    by_handle: dict[str, list[dict[str, Any]]] = {h.lower(): [] for h in handles}
    unknown: list[dict[str, Any]] = []
    for it in items:
        ah = (_author_handle(it) or "").lower()
        if ah and ah in by_handle:
            by_handle[ah].append(it)
        else:
            unknown.append(it)

    if unknown:
        log.warning("Twitter ingest: %d items had unknown/missing author", len(unknown))

    total_returned = 0
    total_rt_filtered = 0
    total_reply_filtered = 0
    total_kept = 0
    total_inserted = 0
    handles_with_zero: list[str] = []

    for acct in accounts:
        h = acct["handle"]
        tier = acct["tier"]
        bucket = by_handle.get(h.lower(), [])
        returned = len(bucket)
        total_returned += returned

        rt_skipped = 0
        reply_skipped = 0
        normalized: list[Mention] = []
        for it in bucket:
            if _is_retweet(it):
                rt_skipped += 1
                continue
            if _is_reply(it):
                reply_skipped += 1
                continue
            m = _to_mention(it, h, tier)
            if m is not None:
                normalized.append(m)
        total_rt_filtered += rt_skipped
        total_reply_filtered += reply_skipped
        total_kept += len(normalized)

        # Dedup count = how many we *try* to insert that aren't already present.
        existing = _existing_source_ids([m.source_id for m in normalized])
        new_inserts_attempted = sum(1 for m in normalized if m.source_id not in existing)
        inserted = _insert_mentions(normalized)
        total_inserted += inserted

        log.info(
            "@%s [tier=%s]: returned=%d, rt_filtered=%d, reply_filtered=%d, "
            "originals=%d, new_inserts=%d, dupes=%d",
            h, tier, returned, rt_skipped, reply_skipped,
            len(normalized), inserted, len(normalized) - inserted,
        )
        if returned == 0:
            handles_with_zero.append(h)

    log.info(
        "Twitter ingest done: returned=%d, rt_filtered=%d, reply_filtered=%d, "
        "originals=%d, inserted=%d, zero_volume_handles=%d",
        total_returned, total_rt_filtered, total_reply_filtered,
        total_kept, total_inserted, len(handles_with_zero),
    )
    if handles_with_zero:
        log.warning("Twitter ingest: handles with 0 tweets returned: %s",
                    ", ".join(handles_with_zero))
