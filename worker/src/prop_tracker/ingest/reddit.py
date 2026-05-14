"""Reddit ingester. Pulls recent posts from configured subreddits via an Apify
actor and writes them into the `mentions` table.

Default actor: `trudax/reddit-scraper-lite` (env: APIFY_REDDIT_ACTOR).
If you swap actors, you may need to tweak the input builder and the field
mapping in `_normalize` below \u2014 different actors expose slightly different
schemas.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..apify import run_actor_sync
from ..config import load_settings, require_apify
from .common import Mention, coerce_int, coerce_str, upsert_mentions

log = logging.getLogger(__name__)

# How many results per subreddit. The actor's `maxItems` is global, so we ask
# for N * subreddit_count.
PER_SUBREDDIT_LIMIT = 100


def _build_input(subreddits: tuple[str, ...]) -> dict[str, Any]:
    start_urls = [
        {"url": f"https://www.reddit.com/r/{sr.strip()}/new/"}
        for sr in subreddits
        if sr.strip()
    ]
    return {
        "startUrls": start_urls,
        "maxItems": PER_SUBREDDIT_LIMIT * max(len(start_urls), 1),
        "scrollTimeout": 40,
        "skipComments": True,
        "skipUserPosts": True,
        "skipCommunity": True,
        "searchPosts": False,
        "searchComments": False,
        "searchCommunities": False,
        "searchUsers": False,
        "sort": "new",
        "includeNSFW": False,
        "debugMode": False,
    }


def _parse_dt(v: Any) -> datetime:
    """Reddit actor returns ISO 8601 string or unix timestamp; handle both."""
    if v is None:
        return datetime.now(timezone.utc)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    s = str(v)
    # Try ISO 8601; fall back to unix-as-string.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromtimestamp(float(s), tz=timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)


def _normalize(item: dict[str, Any]) -> Mention | None:
    """Map an actor output row into our `Mention` dataclass. Be permissive
    about field names so the user can swap actors without rewriting this."""
    source_id = coerce_str(item.get("id") or item.get("postId") or item.get("dataId"))
    if not source_id:
        return None

    title = coerce_str(item.get("title")) or ""
    body = coerce_str(item.get("body") or item.get("text") or item.get("description")) or ""
    text = f"{title}\n\n{body}".strip()
    if not text:
        return None

    return Mention(
        source="reddit",
        source_id=source_id,
        author=coerce_str(item.get("username") or item.get("author")),
        url=coerce_str(item.get("url") or item.get("postUrl")),
        posted_at=_parse_dt(item.get("createdAt") or item.get("created") or item.get("createdAtUtc")),
        raw_text=text,
        engagement_score=(
            coerce_int(item.get("upVotes") or item.get("score") or item.get("ups"))
            + coerce_int(item.get("numberOfComments") or item.get("numComments"))
        ),
    )


def run() -> None:
    settings = load_settings()
    token = require_apify(settings)
    log.info("Reddit ingest: subreddits=%s actor=%s",
             ",".join(settings.reddit_subreddits), settings.apify_reddit_actor)

    items = run_actor_sync(
        actor_id=settings.apify_reddit_actor,
        token=token,
        actor_input=_build_input(settings.reddit_subreddits),
    )

    mentions = [m for m in (_normalize(it) for it in items) if m is not None]
    log.info("Reddit ingest: %d items -> %d normalized mentions", len(items), len(mentions))
    upsert_mentions(mentions)
