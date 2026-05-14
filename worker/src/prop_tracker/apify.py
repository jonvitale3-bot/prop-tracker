"""Thin Apify v2 API client.

We use the synchronous run-and-get-dataset endpoint for simplicity:
    POST /v2/acts/{actor_id}/run-sync-get-dataset-items?token=...
This blocks until the run completes and returns the dataset items as JSON.
Most actors finish a 100-item run in under 2 minutes.

Apify actor IDs are URL-encoded with a `~` in place of `/`, e.g. `trudax~reddit-scraper-lite`.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_BASE = "https://api.apify.com/v2"


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
)
def run_actor_sync(
    actor_id: str,
    token: str,
    actor_input: dict[str, Any],
    *,
    timeout_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Run an actor synchronously and return its default-dataset items."""
    url = f"{_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
    log.info("Apify: running actor %s (timeout=%ss)", actor_id, timeout_seconds)
    with httpx.Client(timeout=httpx.Timeout(timeout_seconds)) as client:
        resp = client.post(url, params={"token": token}, json=actor_input)
        resp.raise_for_status()
        items = resp.json()
    log.info("Apify: actor %s returned %d items", actor_id, len(items))
    return items
