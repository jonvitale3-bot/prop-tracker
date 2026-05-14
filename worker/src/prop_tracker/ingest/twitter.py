"""Twitter ingester via Apify.

Default actor: `kaitoeasyapi~twitter-x-data-tweet-scraper-pay-per-result-cheapest`
which runs on the Apify free plan with pay-per-result pricing
(~$0.30 / 1k tweets). Override with APIFY_TWITTER_ACTOR if you swap.

If you swap actors, you may need to adjust `_build_input` and the field
mapping in `_normalize` since each actor exposes slightly different shapes.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from ..apify import run_actor_sync
from ..config import load_settings, require_apify
from .common import Mention, coerce_int, coerce_str, upsert_mentions

log = logging.getLogger(__name__)

MAX_ITEMS = int(os.environ.get("INGEST_LIMIT_PER_SOURCE", "200"))


def _build_input(queries: tuple[str, ...]) -> dict[str, Any]:
    # kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest schema.
    # Accepts a list of search terms; one tweet may match multiple terms but
    # output is deduped on tweet id, so overlap is harmless.
    return {
        "searchTerms": list(queries),
        "maxItems": MAX_ITEMS,
        "sort": "Latest",
        "tweetLanguage": "en",
        "onlyVerifiedUsers": False,
        "onlyTwitterBlue": False,
        "onlyImage": False,
        "onlyVideo": False,
        "onlyQuote": False,
    }


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


def _normalize(item: dict[str, Any]) -> Mention | None:
    """Best-effort field mapping across common Twitter actor schemas."""
    source_id = coerce_str(
        item.get("id") or item.get("id_str") or item.get("tweetId")
        or item.get("conversation_id")
    )
    if not source_id:
        return None

    text = coerce_str(
        item.get("text") or item.get("full_text") or item.get("fullText")
    )
    if not text:
        # Some actors nest text under legacy.full_text or similar
        legacy = item.get("legacy")
        if isinstance(legacy, dict):
            text = coerce_str(legacy.get("full_text"))
    if not text:
        return None

    author_obj = item.get("author") or item.get("user")
    if isinstance(author_obj, dict):
        author = coerce_str(
            author_obj.get("userName") or author_obj.get("username")
            or author_obj.get("screen_name") or author_obj.get("name")
        )
    else:
        author = coerce_str(author_obj) or coerce_str(item.get("username"))

    url = coerce_str(item.get("url") or item.get("twitterUrl") or item.get("tweetUrl"))
    if not url and author and source_id:
        url = f"https://x.com/{author}/status/{source_id}"

    likes = coerce_int(item.get("likeCount") or item.get("favorite_count")
                       or item.get("favoriteCount"))
    rts = coerce_int(item.get("retweetCount") or item.get("retweet_count"))
    replies = coerce_int(item.get("replyCount") or item.get("reply_count"))

    return Mention(
        source="twitter",
        source_id=source_id,
        author=author,
        url=url,
        posted_at=_parse_dt(item.get("createdAt") or item.get("created_at")
                            or item.get("date")),
        raw_text=text,
        engagement_score=likes + rts + replies,
    )


def run() -> None:
    settings = load_settings()
    token = require_apify(settings)
    log.info("Twitter ingest: queries=%s actor=%s max=%d",
             " | ".join(settings.twitter_queries),
             settings.apify_twitter_actor, MAX_ITEMS)

    items = run_actor_sync(
        actor_id=settings.apify_twitter_actor,
        token=token,
        actor_input=_build_input(settings.twitter_queries),
    )

    mentions = [m for m in (_normalize(it) for it in items) if m is not None]
    log.info("Twitter ingest: %d items -> %d normalized mentions",
             len(items), len(mentions))
    upsert_mentions(mentions)
