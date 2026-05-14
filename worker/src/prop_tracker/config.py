"""Centralised env-var loading.

Most fields are Optional so each command only needs the keys it actually uses
(e.g. `ingest-reddit` doesn't require ANTHROPIC_API_KEY). Call the matching
`require_*` helper at the start of a command when you need a key to be present.
"""

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
# override=True so values in .env beat any stale empty values inherited from
# the parent shell (e.g. an exported but unset ANTHROPIC_API_KEY=). In prod
# there's no .env file, so the Railway-injected env vars still win by default.
load_dotenv(_REPO_ROOT / ".env", override=True)
load_dotenv(_WORKER_DIR / ".env", override=True)


def _placeholder(val: str | None) -> str | None:
    """Treat obvious placeholder values from .env.example as missing.

    Lets the user keep a single .env file without every key filled in;
    placeholders like 'sk-ant-...' or 'apify_api_...' are treated as None
    so per-command `require_*` helpers can fail with a clear message."""
    if val is None:
        return None
    s = val.strip()
    if not s:
        return None
    if s.endswith("...") or s in {"your_odds_api_key_here"}:
        return None
    return s


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    database_url_direct: str | None
    anthropic_api_key: str | None
    anthropic_model: str
    apify_token: str | None
    apify_reddit_actor: str
    apify_twitter_actor: str
    odds_api_key: str | None
    reddit_subreddits: tuple[str, ...]
    twitter_queries: tuple[str, ...]
    log_level: str


def load_settings() -> Settings:
    db_url = _placeholder(os.environ.get("DATABASE_URL"))
    return Settings(
        database_url=db_url,
        database_url_direct=_placeholder(os.environ.get("DATABASE_URL_DIRECT")) or db_url,
        anthropic_api_key=_placeholder(os.environ.get("ANTHROPIC_API_KEY")),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        apify_token=_placeholder(os.environ.get("APIFY_TOKEN")),
        apify_reddit_actor=os.environ.get("APIFY_REDDIT_ACTOR", "trudax~reddit-scraper-lite"),
        apify_twitter_actor=os.environ.get("APIFY_TWITTER_ACTOR", "apidojo~tweet-scraper"),
        odds_api_key=_placeholder(os.environ.get("ODDS_API_KEY")),
        reddit_subreddits=tuple(
            s.strip() for s in os.environ.get("REDDIT_SUBREDDITS", "sportsbook").split(",") if s.strip()
        ),
        twitter_queries=tuple(
            q.strip() for q in os.environ.get("TWITTER_QUERIES", "NBA player prop").split(",") if q.strip()
        ),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )


def _missing(name: str, hint: str) -> None:
    raise RuntimeError(f"Missing required env var: {name}. {hint}")


def require_database(s: Settings) -> str:
    if not s.database_url:
        _missing("DATABASE_URL", "Get it from console.neon.tech (pooled connection string).")
    return s.database_url  # type: ignore[return-value]


def require_database_direct(s: Settings) -> str:
    dsn = s.database_url_direct or s.database_url
    if not dsn:
        _missing("DATABASE_URL_DIRECT", "Use the non-pooled connection string from Neon for migrations.")
    return dsn  # type: ignore[return-value]


def require_apify(s: Settings) -> str:
    if not s.apify_token:
        _missing("APIFY_TOKEN", "Get one from apify.com -> Settings -> Integrations -> API.")
    return s.apify_token  # type: ignore[return-value]


def require_anthropic(s: Settings) -> str:
    if not s.anthropic_api_key:
        _missing("ANTHROPIC_API_KEY", "Get one from console.anthropic.com -> Settings -> API Keys.")
    return s.anthropic_api_key  # type: ignore[return-value]


def require_odds_api(s: Settings) -> str:
    if not s.odds_api_key:
        _missing("ODDS_API_KEY", "Get one from the-odds-api.com.")
    return s.odds_api_key  # type: ignore[return-value]


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
