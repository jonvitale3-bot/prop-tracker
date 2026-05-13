"""Centralised env-var loading. Import `settings` everywhere else."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the worker dir (one above src/) and the repo root, in that order.
_HERE = Path(__file__).resolve()
_WORKER_DIR = _HERE.parents[2]
_REPO_ROOT = _WORKER_DIR.parent
load_dotenv(_REPO_ROOT / ".env", override=False)
load_dotenv(_WORKER_DIR / ".env", override=True)


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


@dataclass(frozen=True)
class Settings:
    database_url: str
    database_url_direct: str
    anthropic_api_key: str
    anthropic_model: str
    apify_token: str
    apify_reddit_actor: str
    apify_twitter_actor: str
    reddit_subreddits: tuple[str, ...]
    twitter_queries: tuple[str, ...]
    log_level: str


def load_settings() -> Settings:
    return Settings(
        database_url=_require("DATABASE_URL"),
        database_url_direct=os.environ.get("DATABASE_URL_DIRECT") or _require("DATABASE_URL"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        apify_token=_require("APIFY_TOKEN"),
        apify_reddit_actor=os.environ.get("APIFY_REDDIT_ACTOR", "trudax~reddit-scraper-lite"),
        apify_twitter_actor=os.environ.get("APIFY_TWITTER_ACTOR", "apidojo~tweet-scraper"),
        reddit_subreddits=tuple(
            s.strip() for s in os.environ.get("REDDIT_SUBREDDITS", "sportsbook").split(",") if s.strip()
        ),
        twitter_queries=tuple(
            q.strip() for q in os.environ.get("TWITTER_QUERIES", "NBA player prop").split(",") if q.strip()
        ),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
