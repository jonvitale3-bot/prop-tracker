"""Apply SQL migrations from shared/sql/ in order.

Each .sql file is named NNN_description.sql. The migration id is the filename
without the extension. Applied ids are recorded in the _migrations table.

The migration files themselves are responsible for wrapping their work in
BEGIN/COMMIT so partial application doesn't leave the schema in a half state.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[3]
_SQL_DIR = _REPO_ROOT / "shared" / "sql"


def _ensure_migrations_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                id          TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )


def _applied_ids(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM _migrations")
        return {row[0] for row in cur.fetchall()}


def _dsn() -> str:
    """Load DATABASE_URL_DIRECT (preferred) or DATABASE_URL. Migrations only
    need this one env var \u2014 we don't pull in full Settings here so the user
    can run migrations before filling in the rest of .env."""
    load_dotenv(_REPO_ROOT / ".env", override=False)
    dsn = os.environ.get("DATABASE_URL_DIRECT") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL_DIRECT (or DATABASE_URL) must be set")
    return dsn


def run() -> None:
    # Use the direct (non-pooled) URL for DDL.
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        _ensure_migrations_table(conn)
        applied = _applied_ids(conn)

        files = sorted(_SQL_DIR.glob("*.sql"))
        if not files:
            log.warning("No .sql files found in %s", _SQL_DIR)
            return

        for path in files:
            mid = path.stem
            if mid in applied:
                log.info("skip   %s (already applied)", mid)
                continue

            log.info("apply  %s", mid)
            sql = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
            log.info("done   %s", mid)

        log.info("migrations complete (%d file(s))", len(files))


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run()
