"""Reddit ingester that hits Reddit's API directly.

Two modes:

  1. OAuth (preferred). Requires REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET,
     REDDIT_USERNAME, REDDIT_PASSWORD env vars. Register a "script" type
     app at https://www.reddit.com/prefs/apps to get client_id/secret.
     Reddit gives OAuth clients ~100 requests/min and routes through
     oauth.reddit.com which doesn't pre-block cloud IPs.

  2. Anonymous. Uses public www.reddit.com/.../new.json. Works locally
     but is reliably 403'd from cloud IPs (Railway, AWS, etc.) by
     Reddit's WAF. Falls back to anonymous only if no client_id is set.
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


def _oauth_token() -> str | None:
    """Fetch a Reddit OAuth bearer token via password grant. Returns None
    if OAuth env vars aren't configured (caller falls back to anonymous)."""
    cid = os.environ.get("REDDIT_CLIENT_ID")
    sec = os.environ.get("REDDIT_CLIENT_SECRET")
    user = os.environ.get("REDDIT_USERNAME")
    pw = os.environ.get("REDDIT_PASSWORD")
    if not all([cid, sec, user, pw]):
        return None
    r = httpx.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(cid, sec),
        data={"grant_type": "password", "username": user, "password": pw},
        headers={"User-Agent": _UA},
        timeout=15.0,
    )
    r.raise_for_status()
    return r.json()["access_token"]


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
)
def _fetch_subreddit(client: httpx.Client, sub: str, limit: int, *, oauth: bool) -> list[dict[str, Any]]:
    host = "oauth.reddit.com" if oauth else "www.reddit.com"
    url = f"https://{host}/r/{sub}/new.json"
    r = client.get(url, params={"limit": str(min(limit, 100))})
    if r.status_code == 429:
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

    token = _oauth_token()
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        mode = "oauth"
    else:
        mode = "anonymous"
    log.info("Reddit (direct, %s) ingest: subs=%s limit/sub=%d",
             mode, ",".join(subs), PER_SUBREDDIT_LIMIT)

    all_items: list[dict[str, Any]] = []
    with httpx.Client(headers=headers, timeout=15.0, follow_redirects=True) as client:
        for sub in subs:
            try:
                items = _fetch_subreddit(client, sub, PER_SUBREDDIT_LIMIT, oauth=bool(token))
                log.info("Reddit (direct): r/%s -> %d posts", sub, len(items))
                all_items.extend(items)
            except Exception:
                log.exception("Reddit (direct): r/%s failed; continuing", sub)

    mentions = [m for m in (_normalize(it) for it in all_items) if m is not None]
    log.info("Reddit (direct): %d raw posts -> %d normalized mentions",
             len(all_items), len(mentions))
    upsert_mentions(mentions)
