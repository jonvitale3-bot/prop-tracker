"""Reddit ingester that hits Reddit's public JSON API directly.

No Apify dependency, no actor compute, no memory quotas. Reddit allows
unauthenticated access to per-subreddit JSON listings at
`https://www.reddit.com/r/<sub>/new.json?limit=100` with a reasonable
User-Agent header. Rate limit is ~60 req/min from a single IP, way more
than we need (one request per subreddit per cycle).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import load_settings
from .common import Mention, coerce_int, coerce_str, upsert_mentions

log = logging.getLogger(__name__)

PER_SUBREDDIT_LIMIT = int(os.environ.get("INGEST_LIMIT_PER_SOURCE", "100"))

# Reddit's UA policy requires the "by /u/<username>" form; generic UAs are 403'd.
# Override via REDDIT_USER_AGENT env var if you want to identify with your real
# Reddit username (raises rate limit ceiling slightly too).
_UA = os.environ.get(
    "REDDIT_USER_AGENT",
    "prop-tracker/0.1 (by /u/anonymous)",
)


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
)
def _fetch_subreddit(client: httpx.Client, sub: str, limit: int) -> list[dict[str, Any]]:
    url = f"https://www.reddit.com/r/{sub}/new.json"
    r = client.get(url, params={"limit": str(min(limit, 100))})
    if r.status_code == 429:
        # Surface rate-limit explicitly so tenacity can back off.
        raise httpx.HTTPStatusError("429 rate limited", request=r.request, response=r)
    r.raise_for_status()
    data = r.json()
    children = data.get("data", {}).get("children", []) or []
    return [c.get("data", {}) for c in children if c.get("kind") == "t3"]


def _normalize(item: dict[str, Any]) -> Mention | None:
    raw_id = coerce_str(item.get("id"))
    if not raw_id:
        return None
    title = coerce_str(item.get("title")) or ""
    body = coerce_str(item.get("selftext")) or ""
    text = f"{title}\n\n{body}".strip()
    if not text:
        return None

    created = item.get("created_utc")
    posted_at = (
        datetime.fromtimestamp(float(created), tz=timezone.utc)
        if isinstance(created, (int, float))
        else datetime.now(timezone.utc)
    )

    permalink = coerce_str(item.get("permalink")) or ""
    url = f"https://www.reddit.com{permalink}" if permalink else None

    return Mention(
        source="reddit",
        source_id=f"t3_{raw_id}",   # match the prefixed form trudax actor used
        author=coerce_str(item.get("author")),
        url=url,
        posted_at=posted_at,
        raw_text=text,
        engagement_score=coerce_int(item.get("score")) + coerce_int(item.get("num_comments")),
    )


def run() -> None:
    settings = load_settings()
    subs = [s for s in settings.reddit_subreddits if s]
    log.info("Reddit (direct) ingest: subs=%s limit/sub=%d", ",".join(subs), PER_SUBREDDIT_LIMIT)

    all_items: list[dict[str, Any]] = []
    with httpx.Client(
        headers={"User-Agent": _UA, "Accept": "application/json"},
        timeout=15.0,
        follow_redirects=True,
    ) as client:
        for sub in subs:
            try:
                items = _fetch_subreddit(client, sub, PER_SUBREDDIT_LIMIT)
                log.info("Reddit (direct): r/%s -> %d posts", sub, len(items))
                all_items.extend(items)
            except Exception:
                log.exception("Reddit (direct): r/%s failed; continuing", sub)

    mentions = [m for m in (_normalize(it) for it in all_items) if m is not None]
    log.info("Reddit (direct): %d raw posts -> %d normalized mentions",
             len(all_items), len(mentions))
    upsert_mentions(mentions)
