"""Twitter ingester. Searches recent tweets via an Apify actor and writes them
into the `mentions` table.

Default actor: `apidojo/tweet-scraper` (env: APIFY_TWITTER_ACTOR).
If you swap actors, tweak `_build_input` and `_normalize` to match the new
actor's input/output schemas.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..apify import run_actor_sync
from ..config import load_settings, require_apify
from .common import Mention, coerce_int, coerce_str, upsert_mentions

log = logging.getLogger(__name__)

LOOKBACK_HOURS = 6
MAX_ITEMS = 100


def _build_input(queries: tuple[str, ...]) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).strftime(
        "%Y-%m-%d_%H:%M:%S_UTC"
    )
    return {
        "searchTerms": list(queries),
        "tweetLanguage": "en",
        "sort": "Latest",
        "maxItems": MAX_ITEMS,
        "start": since,           # apidojo/tweet-scraper expects this format
        "includeSearchTerms": False,
    }


def _parse_dt(v: Any) -> datetime:
    if v is None:
        return datetime.now(timezone.utc)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    s = str(v)
    # Twitter formats: "Mon May 13 19:00:00 +0000 2026" or ISO 8601.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            return datetime.now(timezone.utc)


def _normalize(item: dict[str, Any]) -> Mention | None:
    source_id = coerce_str(item.get("id") or item.get("id_str") or item.get("tweetId"))
    if not source_id:
        return None

    text = coerce_str(
        item.get("text") or item.get("fullText") or item.get("full_text")
    )
    if not text:
        return None

    # Author can be a dict ({userName, screenName, ...}) or a flat string.
    author_obj = item.get("author") or item.get("user")
    if isinstance(author_obj, dict):
        author = coerce_str(
            author_obj.get("userName")
            or author_obj.get("screen_name")
            or author_obj.get("username")
        )
    else:
        author = coerce_str(author_obj)

    return Mention(
        source="twitter",
        source_id=source_id,
        author=author,
        url=coerce_str(item.get("url") or item.get("twitterUrl")),
        posted_at=_parse_dt(item.get("createdAt") or item.get("created_at")),
        raw_text=text,
        engagement_score=(
            coerce_int(item.get("likeCount") or item.get("favorite_count"))
            + coerce_int(item.get("retweetCount") or item.get("retweet_count"))
            + coerce_int(item.get("replyCount") or item.get("reply_count"))
        ),
    )


def run() -> None:
    settings = load_settings()
    token = require_apify(settings)
    log.info("Twitter ingest: queries=%s actor=%s",
             " | ".join(settings.twitter_queries), settings.apify_twitter_actor)

    items = run_actor_sync(
        actor_id=settings.apify_twitter_actor,
        token=token,
        actor_input=_build_input(settings.twitter_queries),
    )

    mentions = [m for m in (_normalize(it) for it in items) if m is not None]
    log.info("Twitter ingest: %d items -> %d normalized mentions", len(items), len(mentions))
    upsert_mentions(mentions)
