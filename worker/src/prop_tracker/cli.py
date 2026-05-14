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
    from .ingest import reddit as _r  # noqa: PLC0415
    _r.run()


def ingest_twitter() -> None:
    _bootstrap()
    from .ingest import twitter as _t  # noqa: PLC0415
    _t.run()


def parse_mentions() -> None:
    _bootstrap()
    from . import parse as _p  # noqa: PLC0415
    _p.run()


def grade_results() -> None:
    _bootstrap()
    from . import grade as _g  # noqa: PLC0415
    _g.run()


def worker_loop() -> None:
    """Long-running orchestrator: ingest -> parse -> (daily) grade."""
    from . import orchestrator  # noqa: PLC0415
    orchestrator.main()
