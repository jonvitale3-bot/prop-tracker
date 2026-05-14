"""Console-script entry points. Each command is a small wrapper around a module."""

from __future__ import annotations

from .config import configure_logging, load_settings


def _bootstrap() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)


def migrate() -> None:
    # Migrations only need DATABASE_URL[_DIRECT]; don't trip on missing
    # Anthropic/Apify vars before the user has filled them in.
    import logging  # noqa: PLC0415
    import os  # noqa: PLC0415
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    from . import migrate as _m  # noqa: PLC0415
    _m.run()


def ingest_reddit() -> None:
    _bootstrap()
    raise SystemExit("ingest-reddit: not yet implemented (milestone 3)")


def ingest_twitter() -> None:
    _bootstrap()
    raise SystemExit("ingest-twitter: not yet implemented (milestone 3)")


def parse_mentions() -> None:
    _bootstrap()
    raise SystemExit("parse-mentions: not yet implemented (milestone 4)")


def grade_results() -> None:
    _bootstrap()
    raise SystemExit("grade-results: not yet implemented (milestone 5)")
