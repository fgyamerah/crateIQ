"""Persistent, library-scoped storage for manually assembled DJ crates."""
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Iterator

from .library_root import selected_library_root

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_crates (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, notes TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_crate_tracks (
    crate_id INTEGER NOT NULL REFERENCES manual_crates(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL, position INTEGER NOT NULL, added_at TEXT NOT NULL, note TEXT,
    PRIMARY KEY (crate_id, track_id), UNIQUE (crate_id, position)
);
CREATE INDEX IF NOT EXISTS idx_manual_crates_updated ON manual_crates(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_manual_crate_tracks_order ON manual_crate_tracks(crate_id, position);
"""


def crates_db_path() -> Path:
    """Return the selected library's crate state DB, never the pipeline DB."""
    return selected_library_root() / "logs" / "manual_crates.db"


@contextlib.contextmanager
def get_crate_conn() -> Iterator[sqlite3.Connection]:
    """Yield a transactional crate-state connection, initializing schema safely."""
    db_path = crates_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
