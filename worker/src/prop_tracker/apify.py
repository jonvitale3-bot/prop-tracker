"""Thin Apify v2 API client.

Uses the asynchronous run pattern so we don't hit the sync endpoint's
~5 minute hard cap:

  1. POST /v2/acts/{actor_id}/runs           -> { id, defaultDatasetId, ... }
  2. Poll GET /v2/actor-runs/{run_id} until status == SUCCEEDED
  3. GET /v2/datasets/{dataset_id}/items     -> list of result objects

Actor IDs use `~` in place of `/`, e.g. `trudax~reddit-scraper-lite`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_BASE = "https://api.apify.com/v2"
_DEFAULT_POLL_INTERVAL = 5      # seconds between status polls
_DEFAULT_MAX_WAIT = 15 * 60     # 15 minutes overall ceiling

# HTTP statuses where retrying is sensible (transient network / 5xx).
_RETRY_TYPES = (httpx.HTTPError, httpx.TimeoutException, httpx.RemoteProtocolError)


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(_RETRY_TYPES),
)
def _post_run(client: httpx.Client, actor_id: str, token: str, actor_input: dict[str, Any]) -> dict[str, Any]:
    url = f"{_BASE}/acts/{actor_id}/runs"
    resp = client.post(url, params={"token": token}, json=actor_input)
    if resp.status_code >= 400:
        # Surface Apify's error body \u2014 it has the real reason (insufficient
        # balance, actor requires rental, invalid input, etc.).
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise RuntimeError(
            f"Apify POST /acts/{actor_id}/runs failed: HTTP {resp.status_code} \u2014 {body}"
        )
    return resp.json()["data"]


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(_RETRY_TYPES),
)
def _get_run(client: httpx.Client, run_id: str, token: str) -> dict[str, Any]:
    url = f"{_BASE}/actor-runs/{run_id}"
    resp = client.get(url, params={"token": token})
    resp.raise_for_status()
    return resp.json()["data"]


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(_RETRY_TYPES),
)
def _get_dataset_items(client: httpx.Client, dataset_id: str, token: str) -> list[dict[str, Any]]:
    url = f"{_BASE}/datasets/{dataset_id}/items"
    resp = client.get(url, params={"token": token, "clean": "true", "format": "json"})
    resp.raise_for_status()
    return resp.json()


def run_actor(
    actor_id: str,
    token: str,
    actor_input: dict[str, Any],
    *,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
    max_wait_seconds: int = _DEFAULT_MAX_WAIT,
) -> list[dict[str, Any]]:
    """Start an actor run, wait for it to finish, return the dataset items.

    Raises RuntimeError if the run fails or doesn't complete within
    `max_wait_seconds`.
    """
    timeout = httpx.Timeout(60.0)
    with httpx.Client(timeout=timeout) as client:
        log.info("Apify: starting actor %s", actor_id)
        run = _post_run(client, actor_id, token, actor_input)
        run_id = run["id"]
        dataset_id = run["defaultDatasetId"]
        log.info("Apify: run %s started (dataset %s); polling", run_id, dataset_id)

        deadline = time.monotonic() + max_wait_seconds
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"Apify run {run_id} did not finish in {max_wait_seconds}s "
                    f"(actor={actor_id})"
                )
            time.sleep(poll_interval)
            run = _get_run(client, run_id, token)
            status = run["status"]
            log.debug("Apify: run %s status=%s", run_id, status)
            if status == "SUCCEEDED":
                log.info("Apify: run %s succeeded", run_id)
                break
            if status in {"FAILED", "ABORTED", "TIMED-OUT"}:
                raise RuntimeError(
                    f"Apify run {run_id} ended with status={status} "
                    f"(actor={actor_id}); exit_code={run.get('exitCode')}"
                )
            # else still RUNNING / READY — keep polling

        items = _get_dataset_items(client, dataset_id, token)
        log.info("Apify: dataset %s returned %d items", dataset_id, len(items))
        return items


# Back-compat alias for the previous sync-only API.
run_actor_sync = run_actor
