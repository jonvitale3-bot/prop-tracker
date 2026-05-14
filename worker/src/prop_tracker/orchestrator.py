"""Long-running worker loop. One Railway service runs this forever.

Every cycle (30 min by default):
  1. ingest-reddit  -> pull new posts to `mentions`
  2. parse-mentions -> Claude Haiku extracts plays
  3. grade-results  -> runs ONCE per day, on the first cycle after 04:00 ET

Each step is wrapped in try/except so one failure doesn't take down the loop.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from .config import configure_logging, load_settings

log = logging.getLogger(__name__)

CYCLE_SECONDS = int(os.environ.get("WORKER_CYCLE_SECONDS", str(30 * 60)))  # 30min
GRADE_HOUR_ET = int(os.environ.get("WORKER_GRADE_HOUR_ET", "4"))
_ET = ZoneInfo("America/New_York")


def _safe(name: str, fn: Callable[[], None]) -> None:
    log.info("orchestrator: -> %s", name)
    try:
        fn()
        log.info("orchestrator: <- %s ok", name)
    except Exception:
        log.exception("orchestrator: <- %s FAILED", name)


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    log.info(
        "orchestrator: starting (cycle=%ds, grade_hour_ET=%d)",
        CYCLE_SECONDS, GRADE_HOUR_ET,
    )

    last_graded_date = None

    while True:
        cycle_start = time.monotonic()
        log.info("orchestrator: --- cycle start ---")

        # Use Reddit's public JSON API directly (no Apify dependency).
        # Set INGEST_REDDIT_BACKEND=apify to fall back to the trudax actor.
        from . import parse  # noqa: PLC0415
        if os.environ.get("INGEST_REDDIT_BACKEND", "direct").lower() == "apify":
            from .ingest import reddit as ingest_reddit  # noqa: PLC0415
        else:
            from .ingest import reddit_direct as ingest_reddit  # noqa: PLC0415
        _safe("ingest-reddit", ingest_reddit.run)
        _safe("parse-mentions", parse.run)

        # Grade once per day, on the first cycle at/after the configured hour.
        now_et = datetime.now(_ET)
        today_et = now_et.date()
        if now_et.hour >= GRADE_HOUR_ET and last_graded_date != today_et:
            from . import grade  # noqa: PLC0415
            _safe("grade-results", grade.run)
            last_graded_date = today_et

        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0, CYCLE_SECONDS - elapsed)
        log.info("orchestrator: cycle done in %.1fs, sleeping %.1fs",
                 elapsed, sleep_for)
        time.sleep(sleep_for)
