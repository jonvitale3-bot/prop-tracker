"""Thin psycopg helpers. Raw SQL only \u2014 no ORM."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from .config import load_settings


@contextmanager
def connect(direct: bool = False) -> Iterator[psycopg.Connection]:
    """Open a connection. Use direct=True for migrations / long-running jobs."""
    settings = load_settings()
    dsn = settings.database_url_direct if direct else settings.database_url
    with psycopg.connect(dsn, autocommit=False, row_factory=dict_row) as conn:
        yield conn


def fetch_all(sql: str, params: tuple | dict | None = None, *, direct: bool = False) -> list[dict]:
    with connect(direct=direct) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def execute(sql: str, params: tuple | dict | None = None, *, direct: bool = False) -> int:
    with connect(direct=direct) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount
