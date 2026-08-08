"""
Persisted, app-owned history for Cycle 10 batch Inbox preparation operations
("Process All", "Clean Selected", "Enrich Selected").

Lives in jobs.db's `preparation_operations` table, never in the trusted
pipeline processed.db. Mirrors analysis_operations_service's lifecycle
exactly: a row is created only for a confirmed run about to begin work,
progress is truthful subset counts (never a fabricated percent), and a
restart closes out any row left 'running' rather than silently resuming it.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..core.db import get_conn

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
_RESTART_ERROR_REASON = "backend_restarted"
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


def start_operation(operation_type: str, *, track_count: int) -> dict[str, Any]:
    operation_id = uuid.uuid4().hex
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO preparation_operations
               (id, operation_type, status, track_count, created_at, started_at)
               VALUES (?, ?, 'running', ?, ?, ?)""",
            (operation_id, operation_type, track_count, now, now),
        )
    return {"id": operation_id}


def update_progress(
    operation_id: str,
    *,
    cleaned_count: int,
    enriched_count: int,
    written_count: int,
    needs_review_count: int,
    ready_count: int,
    failed_count: int,
) -> None:
    """Scoped to status='running' so a late update can never resurrect a closed row."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE preparation_operations
               SET cleaned_count = ?, enriched_count = ?, written_count = ?,
                   needs_review_count = ?, ready_count = ?, failed_count = ?
               WHERE id = ? AND status = 'running'""",
            (cleaned_count, enriched_count, written_count, needs_review_count,
             ready_count, failed_count, operation_id),
        )


def finish_operation(
    operation_id: str,
    *,
    status: str,
    cleaned_count: int,
    enriched_count: int,
    written_count: int,
    needs_review_count: int,
    ready_count: int,
    failed_count: int,
    warnings: list[str],
    error_reason: Optional[str] = None,
) -> None:
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"finish_operation requires a terminal status, got {status!r}")
    payload = json.dumps(warnings[:_MAX_WARNINGS])
    reason = error_reason[:_MAX_ERROR_REASON_LEN] if error_reason else None
    with get_conn() as conn:
        conn.execute(
            """UPDATE preparation_operations
               SET status = ?, cleaned_count = ?, enriched_count = ?, written_count = ?,
                   needs_review_count = ?, ready_count = ?, failed_count = ?,
                   warnings_json = ?, error_reason = ?, finished_at = ?
               WHERE id = ?""",
            (status, cleaned_count, enriched_count, written_count, needs_review_count,
             ready_count, failed_count, payload, reason, _now(), operation_id),
        )


def is_cancel_requested(operation_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT cancel_requested FROM preparation_operations WHERE id = ?", (operation_id,)
        ).fetchone()
    return bool(row and row["cancel_requested"])


def request_cancel(operation_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM preparation_operations WHERE id = ?", (operation_id,)).fetchone()
        if row is None:
            return None
        if row["status"] == "running":
            conn.execute(
                "UPDATE preparation_operations SET cancel_requested = 1 WHERE id = ?", (operation_id,)
            )
            row = conn.execute("SELECT * FROM preparation_operations WHERE id = ?", (operation_id,)).fetchone()
    return _row_to_dict(row)


def get_operation(operation_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM preparation_operations WHERE id = ?", (operation_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_recent(limit: int = 20) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM preparation_operations ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def recover_interrupted_operations() -> int:
    """Close out operations left 'running' by a previous backend process."""
    now = _now()
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM preparation_operations WHERE status = 'running'").fetchall()
        for row in rows:
            conn.execute(
                """UPDATE preparation_operations
                   SET status = 'failed', error_reason = ?, finished_at = ?
                   WHERE id = ?""",
                (_RESTART_ERROR_REASON, now, row["id"]),
            )
    return len(rows)
