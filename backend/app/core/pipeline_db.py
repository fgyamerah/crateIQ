"""
Read-only connection to the toolkit's pipeline database (processed.db).

The backend NEVER writes to this database — it belongs to the pipeline.
All queries here are SELECT-only.  If the DB does not exist yet (no
pipeline run has happened), callers receive an empty result set rather
than an error.
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
from pathlib import Path
from typing import Iterator

from .library_root import library_db_path

log = logging.getLogger(__name__)


@contextlib.contextmanager
def get_pipeline_conn() -> Iterator[sqlite3.Connection]:
    """
    Yield a read-only WAL connection to processed.db.

    Raises FileNotFoundError if the database does not exist — callers
    should catch this and return an appropriate empty response.
    """
    db_path = library_db_path()
    if not db_path.exists():
        raise FileNotFoundError(
            f"Pipeline database not found at {db_path}. "
            "Run the pipeline at least once to create it."
        )
    # ``Path.as_uri`` encodes URI-reserved characters in a valid configured
    # local root (for example '#'), while uri=True + mode=ro prevents writes.
    conn = sqlite3.connect(
        db_path.resolve().as_uri() + "?mode=ro",
        uri=True,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def pipeline_db_exists() -> bool:
    return library_db_path().exists()


def pipeline_db_path() -> Path:
    return library_db_path()
