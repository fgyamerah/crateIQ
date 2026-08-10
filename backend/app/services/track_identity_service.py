"""Optional, local-only acoustic-fingerprint identity strengthening.

Metadata model Phase 2. Track numeric `id` (the existing `tracks.id` primary
key) remains the sole stable local track identity and path-independent
contract used everywhere else in the backend -- this module never replaces
or competes with it. A Chromaprint fingerprint is, at most, optional
*corroborating* identity evidence: it is not always available (no `fpcalc`
tool, unreadable/unsupported audio) and is not treated as unique enough to
be a primary key on its own.

Reuses the exact same local, no-network `fpcalc_available()` /
`fingerprint_file()` capability the AcoustID provider adapter already uses
(see `providers/acoustid_client.py`) -- no new dependency, no `beet` CLI, no
second fingerprinting implementation. Fingerprinting is always an explicit,
single-track, local-only action: never automatic, never required to open or
use the library, and it never contacts AcoustID or any other network
service (that only happens via the separate, credential-gated
`acoustid_client.lookup()` provider path, unchanged by this module). A
missing `fpcalc` tool is reported as a truthful "unavailable" status, never
an exception.

Owns its own additive `track_fingerprints` table in the selected library's
local index, following the same pattern as `quality_findings_service` /
`bpm_retry_policy_service`: schema creation only on a write path, read paths
never create the table.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from .providers import acoustid_client

_TABLE = "track_fingerprints"
_FINGERPRINT_MAX_LEN = 4000
_ALGORITHM = "chromaprint"


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
        conn.row_factory = sqlite3.Row
        yield conn
        return
    with sqlite3.connect(_db_path()) as owned:
        owned.row_factory = sqlite3.Row
        yield owned


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            track_id      INTEGER PRIMARY KEY,
            fingerprint   TEXT    NOT NULL,
            duration_sec  REAL,
            algorithm     TEXT    NOT NULL DEFAULT '{_ALGORITHM}',
            computed_at   TEXT    NOT NULL
        )
        """
    )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "track_id": row["track_id"],
        "fingerprint": row["fingerprint"],
        "duration_sec": row["duration_sec"],
        "algorithm": row["algorithm"],
        "computed_at": row["computed_at"],
    }


def fpcalc_available() -> bool:
    """Local, credential-free check -- never contacts the network."""
    return acoustid_client.fpcalc_available()


def get_for_track(track_id: int, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """Read-only; never creates the table -- absence means "no fingerprint computed yet"."""
    try:
        with _connection(conn) as active:
            tables = {row[0] for row in active.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if _TABLE not in tables:
                return None
            row = active.execute(f"SELECT * FROM {_TABLE} WHERE track_id = ?", (track_id,)).fetchone()
            return _row_to_dict(row) if row is not None else None
    except ValueError:
        return None


def compute_and_store(path: Path, track_id: int, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Explicitly compute and cache a local Chromaprint fingerprint for one track.

    `path` must already be validated by the caller (e.g.
    `track_source_service.validated_track_source()`) to be a real file under
    the selected library root -- this function never resolves or trusts a
    caller-supplied path on its own. Local-only, read-only against the
    audio file, no network call. Returns a truthful status dict rather than
    raising when the tool is simply unavailable.
    """
    if not fpcalc_available():
        return {"status": "unavailable", "track_id": track_id, "message": "fpcalc (Chromaprint) is not installed or not on PATH."}

    fingerprint, duration_sec, error = acoustid_client.fingerprint_file(path)
    if error or not fingerprint:
        return {"status": "error", "track_id": track_id, "message": error or "fpcalc produced no fingerprint."}

    bounded = fingerprint[:_FINGERPRINT_MAX_LEN]
    now = _now()
    with _connection(conn) as active:
        _ensure_table(active)
        active.execute(
            f"""
            INSERT INTO {_TABLE} (track_id, fingerprint, duration_sec, algorithm, computed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                fingerprint = excluded.fingerprint,
                duration_sec = excluded.duration_sec,
                computed_at = excluded.computed_at
            """,
            (track_id, bounded, duration_sec, _ALGORITHM, now),
        )
        row = active.execute(f"SELECT * FROM {_TABLE} WHERE track_id = ?", (track_id,)).fetchone()
        result = _row_to_dict(row)
    result["status"] = "ok"
    return result
