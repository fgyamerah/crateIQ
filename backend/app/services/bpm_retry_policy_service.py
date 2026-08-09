"""Durable BPM automatic-retry pause/resume policy.

This is a small, neutral execution-policy table -- deliberately separate
from `quality_findings_service`'s human Quality Review decisions
(`reviewed`/`ignore`/`review_later`/`unresolved`). A track is paused here
only after `analysis_jobs_service` has proven a genuine two-stage BPM decode
failure (direct aubio decode-error evidence, then a failed FFmpeg recovery
attempt); pausing/resuming never touches that review decision or note, and
review decisions never implicitly enable/disable this pause.

Lives in the selected library's local index (the same `processed.db` as
`quality_review_findings`), never in the trusted pipeline data itself. Schema
creation is additive/idempotent. Read paths (`is_paused`, `paused_track_ids`)
must never create the table -- absence of the table means "no retry pauses
recorded," matching `quality_findings_service.list_findings`'s contract.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root

_TABLE = "bpm_retry_pauses"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    root = selected_library_root()
    path = assert_path_under_root(library_db_path(root), root)
    if not path.is_file():
        raise ValueError("Configured library is not initialized.")
    return path


@contextmanager
def _connection(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        yield conn
        return
    with sqlite3.connect(_db_path()) as owned:
        yield owned


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            track_id INTEGER PRIMARY KEY,
            reason TEXT NOT NULL DEFAULT 'audio_decode_failed',
            paused_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def pause(track_id: int, *, conn: sqlite3.Connection | None = None) -> None:
    """Idempotently create/refresh a durable automatic-retry pause for one track.

    Repeat calls (a recurring genuine failure) simply refresh `updated_at`
    rather than erroring or duplicating a row.
    """
    now = _now()
    with _connection(conn) as active:
        _ensure_table(active)
        active.execute(
            f"""
            INSERT INTO {_TABLE} (track_id, reason, paused_at, updated_at)
            VALUES (?, 'audio_decode_failed', ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (track_id, now, now),
        )


def resume(track_id: int, *, conn: sqlite3.Connection | None = None) -> bool:
    """Idempotently clear one track's pause. Returns True if a pause existed.

    No analysis, no BPM/tag write, no review-decision change -- clears only
    this execution-policy state.
    """
    with _connection(conn) as active:
        _ensure_table(active)
        cursor = active.execute(f"DELETE FROM {_TABLE} WHERE track_id = ?", (track_id,))
        return cursor.rowcount > 0


def is_paused(track_id: int, *, conn: sqlite3.Connection | None = None) -> bool:
    """Read-only check; never creates the table."""
    try:
        with _connection(conn) as active:
            tables = {row[0] for row in active.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if _TABLE not in tables:
                return False
            row = active.execute(f"SELECT 1 FROM {_TABLE} WHERE track_id = ?", (track_id,)).fetchone()
            return row is not None
    except ValueError:
        return False


def paused_track_ids(*, conn: sqlite3.Connection | None = None) -> set[int]:
    """Return every currently-paused track id. Read-only; never creates the table."""
    try:
        with _connection(conn) as active:
            tables = {row[0] for row in active.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if _TABLE not in tables:
                return set()
            return {row[0] for row in active.execute(f"SELECT track_id FROM {_TABLE}")}
    except ValueError:
        return set()
