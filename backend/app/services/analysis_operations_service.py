"""Persisted, app-owned history for explicit, confirmed Analysis Jobs runs.

Lives in jobs.db's `analysis_operations` table, never in the trusted
pipeline processed.db. Only a confirmed run that has passed its pre-flight
checks (tool available, library initialized) and is genuinely about to
attempt work creates a row -- candidate previews and rejected/unconfirmed
run attempts are never persisted, matching the existing product decision.

No absolute source paths, secrets, or full per-track logs are stored --
only bounded counts and the same short warning strings already returned by
the existing preview/run contracts (filenames only, already safe to show).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..core.db import get_conn

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
_RESTART_ERROR_REASON = "backend_restarted"
_USER_CANCELLED_REASON = "user_cancelled"
_MAX_WARNINGS = 50
_MAX_ERROR_REASON_LEN = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    warnings_json = data.pop("warnings_json", None)
    try:
        data["warnings"] = json.loads(warnings_json) if warnings_json else []
    except (TypeError, ValueError):
        data["warnings"] = []
    data["cancel_requested"] = bool(data.get("cancel_requested"))
    return data


def start_operation(
    job_type: str, *, scope_limit: int, eligible_total: int, considered: int
) -> dict[str, Any]:
    """Create a 'running' operation row for a confirmed run that is about to begin work."""
    operation_id = uuid.uuid4().hex
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO analysis_operations
               (id, job_type, mode, status, scope_limit, eligible_total, considered,
                created_at, started_at)
               VALUES (?, ?, 'apply', 'running', ?, ?, ?, ?, ?)""",
            (operation_id, job_type, scope_limit, eligible_total, considered, now, now),
        )
    return {"id": operation_id}


def update_progress(
    operation_id: str, *, processed: int, succeeded: int, skipped: int, failed: int
) -> None:
    """Persist truthful incremental counts so a concurrent read reflects real progress.

    Scoped to status='running' so a late-arriving update can never resurrect
    a row a cancellation or restart-recovery pass already closed out.
    """
    with get_conn() as conn:
        conn.execute(
            """UPDATE analysis_operations
               SET processed = ?, succeeded = ?, skipped = ?, failed = ?
               WHERE id = ? AND status = 'running'""",
            (processed, succeeded, skipped, failed, operation_id),
        )


def finish_operation(
    operation_id: str,
    *,
    status: str,
    processed: int,
    succeeded: int,
    skipped: int,
    failed: int,
    remaining_missing: Optional[int],
    warnings: list[str],
    error_reason: Optional[str] = None,
) -> None:
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"finish_operation requires a terminal status, got {status!r}")
    payload = json.dumps(warnings[:_MAX_WARNINGS])
    reason = error_reason[:_MAX_ERROR_REASON_LEN] if error_reason else None
    with get_conn() as conn:
        conn.execute(
            """UPDATE analysis_operations
               SET status = ?, processed = ?, succeeded = ?, skipped = ?, failed = ?,
                   remaining_missing = ?, warnings_json = ?, error_reason = ?, finished_at = ?
               WHERE id = ?""",
            (
                status, processed, succeeded, skipped, failed,
                remaining_missing, payload, reason, _now(), operation_id,
            ),
        )


def is_cancel_requested(operation_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT cancel_requested FROM analysis_operations WHERE id = ?", (operation_id,)
        ).fetchone()
    return bool(row and row["cancel_requested"])


def request_cancel(operation_id: str) -> Optional[dict[str, Any]]:
    """Idempotently request cancellation.

    Setting the flag on an already-flagged or already-terminal row is a safe
    no-op; the caller always gets back the current record rather than an
    error, since asking a finished operation to stop is not a failure.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM analysis_operations WHERE id = ?", (operation_id,)).fetchone()
        if row is None:
            return None
        if row["status"] == "running":
            conn.execute(
                "UPDATE analysis_operations SET cancel_requested = 1 WHERE id = ?", (operation_id,)
            )
            row = conn.execute("SELECT * FROM analysis_operations WHERE id = ?", (operation_id,)).fetchone()
    return _row_to_dict(row)


def get_operation(operation_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM analysis_operations WHERE id = ?", (operation_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_recent(limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM analysis_operations ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def recover_interrupted_operations() -> int:
    """Close out operations left 'running' by a previous backend process.

    A restart must never silently resume BPM/key analysis, and a row must
    never be left permanently claiming 'running' after the process that
    owned it is gone. Resuming requires a renewed explicit confirmed run.
    """
    now = _now()
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM analysis_operations WHERE status = 'running'").fetchall()
        for row in rows:
            conn.execute(
                """UPDATE analysis_operations
                   SET status = 'failed', error_reason = ?, finished_at = ?
                   WHERE id = ?""",
                (_RESTART_ERROR_REASON, now, row["id"]),
            )
    return len(rows)
