"""Durable, per-track audio-quality findings that survive ffprobe refreshes.

`quality_review_service`'s snapshot model fully replaces its `items_json`
from a fresh ffprobe scan on every `refresh_preview()` call, so it cannot
durably host a per-event finding (e.g. a BPM-analysis decode warning) without
losing it on the next refresh. This module is the neutral persistence layer
for that durable finding class: it owns its own table in the selected
library index, is idempotent/upsertable per (track_id, reason_code, source),
and never imports `quality_review_service` or `analysis_jobs_service` so
either of those can safely import this module without creating a cycle.

Every public function accepts an optional already-open `conn`. Callers that
already hold a connection to the selected library's index (e.g.
`analysis_jobs_service`, mid-BPM-run) should pass it so the finding write
joins that same transaction/db-path resolution instead of opening a second,
independently-resolved connection. Callers without one (e.g.
`quality_review_service`, from a single read request) get a private
short-lived connection opened via the same selected-library-root contract
used elsewhere in this service layer.

Local-index only; never touches media files or tags.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root

_DECISIONS = {"reviewed", "ignore", "review_later", "unresolved"}
_SEVERITIES = {"warning", "error"}
_REASON_CODES = {"recoverable_audio_decode_warning", "audio_decode_failed"}
_DETAILS_MAX_LEN = 500


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


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quality_review_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            reason_code TEXT NOT NULL,
            source TEXT NOT NULL,
            severity TEXT NOT NULL CHECK (severity IN ('warning', 'error')),
            blocking INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL,
            details_json TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            decision TEXT NOT NULL DEFAULT 'unresolved' CHECK (decision IN ('reviewed', 'ignore', 'review_later', 'unresolved')),
            note TEXT NOT NULL DEFAULT '',
            decision_updated_at TEXT,
            UNIQUE (track_id, reason_code, source)
        )
        """
    )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    try:
        details = json.loads(row["details_json"]) if row["details_json"] else None
    except (TypeError, json.JSONDecodeError):
        details = None
    return {
        "finding_key": f"durable:{row['id']}",
        "finding_id": row["id"],
        "track_id": row["track_id"],
        "reason_code": row["reason_code"],
        "source": row["source"],
        "severity": row["severity"],
        "blocking": bool(row["blocking"]),
        "message": row["message"],
        "details": details,
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "occurrence_count": row["occurrence_count"],
        "decision": row["decision"],
        "note": row["note"],
        "reviewed_at": row["decision_updated_at"],
    }


def record_finding(
    track_id: int, reason_code: str, source: str, severity: str, message: str,
    *, blocking: bool = False, details: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Idempotently upsert a durable finding; never raises for a duplicate event.

    A repeated (track_id, reason_code, source) event updates `last_seen_at`
    and increments `occurrence_count` rather than inserting a duplicate
    user-visible row. An existing review `decision` is never reset by this
    call -- only the initial insert defaults to `unresolved`.
    """
    if reason_code not in _REASON_CODES:
        raise ValueError(f"Unknown durable quality reason_code: {reason_code!r}")
    if severity not in _SEVERITIES:
        raise ValueError(f"Unknown durable quality severity: {severity!r}")
    details_json = json.dumps(details)[:_DETAILS_MAX_LEN] if details else None
    now = _now()
    with _connection(conn) as active:
        _ensure_tables(active)
        active.execute(
            """
            INSERT INTO quality_review_findings
                (track_id, reason_code, source, severity, blocking, message, details_json,
                 first_seen_at, last_seen_at, occurrence_count, decision, note, decision_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'unresolved', '', NULL)
            ON CONFLICT(track_id, reason_code, source) DO UPDATE SET
                severity = excluded.severity,
                blocking = excluded.blocking,
                message = excluded.message,
                details_json = excluded.details_json,
                last_seen_at = excluded.last_seen_at,
                occurrence_count = occurrence_count + 1
            """,
            (track_id, reason_code, source, severity, int(blocking), message, details_json, now, now),
        )
        row = active.execute(
            "SELECT * FROM quality_review_findings WHERE track_id = ? AND reason_code = ? AND source = ?",
            (track_id, reason_code, source),
        ).fetchone()
        return _row_to_dict(row)


def list_findings(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Return every durable finding, orphaned track_ids included.

    Orphan filtering (a track that no longer exists) is the caller's
    responsibility -- this module has no opinion on track lifecycle.
    """
    try:
        with _connection(conn) as active:
            tables = {row[0] for row in active.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "quality_review_findings" not in tables:
                return []
            rows = active.execute("SELECT * FROM quality_review_findings ORDER BY id").fetchall()
            return [_row_to_dict(row) for row in rows]
    except ValueError:
        return []


def update_decision(
    finding_id: int, decision: str, note: str = "", *, conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    if decision not in _DECISIONS:
        raise ValueError("Decision must be reviewed, ignore, review_later, or unresolved.")
    with _connection(conn) as active:
        _ensure_tables(active)
        existing = active.execute("SELECT * FROM quality_review_findings WHERE id = ?", (finding_id,)).fetchone()
        if existing is None:
            raise LookupError("Durable quality finding was not found.")
        active.execute(
            "UPDATE quality_review_findings SET decision = ?, note = ?, decision_updated_at = ? WHERE id = ?",
            (decision, note.strip(), _now(), finding_id),
        )
        row = active.execute("SELECT * FROM quality_review_findings WHERE id = ?", (finding_id,)).fetchone()
        return _row_to_dict(row)


def record_audio_decode_warning(
    track_id: int, bpm: float, diagnostic: str = "", *, conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Non-blocking: direct aubio showed decode-error evidence, FFmpeg recovered it."""
    message = (
        "Malformed audio frames were detected. FFmpeg recovered enough audio for "
        f"BPM analysis (measured at {bpm:.2f}). The original file was not modified."
    )
    details = {"diagnostic": diagnostic[:_DETAILS_MAX_LEN]} if diagnostic else None
    return record_finding(
        track_id, "recoverable_audio_decode_warning", "bpm_analysis", "warning", message,
        blocking=False, details=details, conn=conn,
    )


def record_audio_decode_failure(
    track_id: int, diagnostic: str = "", *, conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """direct aubio and FFmpeg both failed to decode the audio; BPM was not changed."""
    message = "Audio could not be decoded reliably by aubio or FFmpeg. BPM was not changed."
    details = {"diagnostic": diagnostic[:_DETAILS_MAX_LEN]} if diagnostic else None
    return record_finding(
        track_id, "audio_decode_failed", "bpm_analysis", "error", message,
        blocking=False, details=details, conn=conn,
    )
