"""Persisted, app-owned history for explicit, confirmed bulk waveform
generation ("Generate missing waveforms") runs.

Lives in jobs.db's `waveform_operations` table, mirroring the Cycle 2
analysis_operations / publish_operations pattern -- never in the trusted
pipeline processed.db. Only a confirmed run that is genuinely about to
attempt work creates a row; read-only previews are never persisted.

No absolute source paths, cache paths, or content hashes are stored here,
only bounded counts -- the same privacy contract the per-track waveform job
rows already follow.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..core.db import get_conn
from ..core.library_key import current_library_key

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
_RESTART_ERROR_REASON = "backend_restarted"
_MAX_ERROR_REASON_LEN = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["cancel_requested"] = bool(data.get("cancel_requested"))
    return data


def start_operation(*, total_tracks: int, eligible_total: int) -> dict[str, Any]:
    """Create a 'running' operation row for a confirmed run about to begin work."""
    operation_id = uuid.uuid4().hex
    now = _now()
    library_key = current_library_key()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO waveform_operations
               (id, operation_type, status, total_tracks, eligible_total,
                created_at, started_at, library_key)
               VALUES (?, 'generate_missing', 'running', ?, ?, ?, ?, ?)""",
            (operation_id, total_tracks, eligible_total, now, now, library_key),
        )
    return {"id": operation_id}


def update_progress(
    operation_id: str, *, processed: int, generated: int, skipped: int, failed: int,
    library_key: str | None = None,
) -> None:
    """Persist truthful incremental counts so a concurrent read reflects real progress.

    Scoped to status='running' so a late-arriving update can never resurrect
    a row a cancellation or restart-recovery pass already closed out.
    Uses the originating library_key from the row if not explicitly provided."""
    with get_conn() as conn:
        if library_key is None:
            row = conn.execute("SELECT library_key FROM waveform_operations WHERE id = ? AND status = 'running'", (operation_id,)).fetchone()
            if row is None:
                return
            library_key = row["library_key"]
        conn.execute(
            """UPDATE waveform_operations
               SET processed = ?, generated = ?, skipped = ?, failed = ?
               WHERE id = ? AND status = 'running' AND library_key = ?""",
            (
                processed,
                generated,
                skipped,
                failed,
                operation_id,
                library_key,
            ),
        )


def finish_operation(
    operation_id: str,
    *,
    status: str,
    processed: int,
    generated: int,
    skipped: int,
    failed: int,
    remaining_missing: Optional[int],
    error_reason: Optional[str] = None,
    library_key: str | None = None,
) -> None:
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"finish_operation requires a terminal status, got {status!r}")
    reason = error_reason[:_MAX_ERROR_REASON_LEN] if error_reason else None
    with get_conn() as conn:
        if library_key is None:
            row = conn.execute("SELECT library_key FROM waveform_operations WHERE id = ?", (operation_id,)).fetchone()
            if row is None:
                return
            library_key = row["library_key"]
        conn.execute(
            """UPDATE waveform_operations
               SET status = ?, processed = ?, generated = ?, skipped = ?, failed = ?,
                   remaining_missing = ?, error_reason = ?, finished_at = ?
               WHERE id = ? AND library_key = ?""",
            (
                status, processed, generated, skipped, failed,
                remaining_missing,
                reason,
                _now(),
                operation_id,
                library_key,
            ),
        )


def is_cancel_requested(operation_id: str, *, library_key: str | None = None) -> bool:
    with get_conn() as conn:
        if library_key is None:
            row = conn.execute("SELECT library_key FROM waveform_operations WHERE id = ?", (operation_id,)).fetchone()
            if row is None:
                return False
            library_key = row["library_key"]
        row = conn.execute(
            "SELECT cancel_requested FROM waveform_operations WHERE id = ? AND library_key = ?",
            (operation_id, library_key),
        ).fetchone()
    return bool(row and row["cancel_requested"])


def request_cancel(operation_id: str) -> Optional[dict[str, Any]]:
    """Idempotently request cancellation.

    Setting the flag on an already-flagged or already-terminal row is a safe
    no-op; the caller always gets back the current record rather than an
    error, since asking a finished operation to stop is not a failure.
    Uses the originating library_key from the row."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM waveform_operations WHERE id = ?", (operation_id,)).fetchone()
        if row is None:
            return None
        library_key = row["library_key"]
        if row["status"] == "running":
            conn.execute(
                "UPDATE waveform_operations SET cancel_requested = 1 WHERE id = ? AND library_key = ?", (operation_id, library_key)
            )
            row = conn.execute("SELECT * FROM waveform_operations WHERE id = ? AND library_key = ?", (operation_id, library_key)).fetchone()
    return _row_to_dict(row)


def get_operation(operation_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM waveform_operations WHERE id = ? AND library_key = ?", (operation_id, current_library_key())).fetchone()
    return _row_to_dict(row) if row else None


def list_recent(limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM waveform_operations WHERE library_key = ? ORDER BY created_at DESC, id DESC LIMIT ?", (current_library_key(), limit)
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def recover_interrupted_operations() -> int:
    """Close out bulk waveform operations left 'running' by a previous process.

    A restart must never silently resume bulk generation, and a row must
    never permanently claim 'running' after the process that owned it is
    gone. Already-generated waveform caches remain valid -- a later Generate
    Missing run simply continues with the remaining tracks.
    """
    now = _now()
    with get_conn() as conn:
        key = current_library_key()
        rows = conn.execute("SELECT id FROM waveform_operations WHERE status = 'running' AND library_key = ?", (key,)).fetchall()
        for row in rows:
            conn.execute(
                """UPDATE waveform_operations
                   SET status = 'failed', error_reason = ?, finished_at = ?
                   WHERE id = ? AND library_key = ?""",
                (_RESTART_ERROR_REASON, now, row["id"], key),
            )
    return len(rows)
