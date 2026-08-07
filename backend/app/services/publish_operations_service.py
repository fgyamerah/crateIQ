"""
Persisted, app-owned history for explicit, confirmed Guided Publish
operations (crate export and SSD sync).

Lives in jobs.db's `publish_operations` table, never in the trusted
pipeline processed.db. Only a confirmed operation that actually attempted
a write creates a row -- readiness/preview calls are never persisted.

No absolute source/destination paths are stored, only a root-relative or
category destination string, matching the analysis_operations privacy
contract this table was modeled on.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..core.db import get_conn

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
_MAX_WARNINGS = 50
_MAX_DETAILS = 50
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
    details_json = data.pop("verification_details_json", None)
    try:
        data["verification_details"] = json.loads(details_json) if details_json else []
    except (TypeError, ValueError):
        data["verification_details"] = []
    return data


def start_operation(
    operation_type: str,
    *,
    export_target: Optional[str] = None,
    sync_source: Optional[str] = None,
    job_id: Optional[str] = None,
    crate_id: Optional[int] = None,
    crate_name: Optional[str] = None,
    scope: Optional[str] = None,
    track_count: int = 0,
) -> dict[str, Any]:
    """Create a 'running' row for a confirmed operation about to attempt a write."""
    if operation_type not in ("export", "sync"):
        raise ValueError(f"Unknown operation_type: {operation_type!r}")
    operation_id = uuid.uuid4().hex
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO publish_operations
               (id, operation_type, export_target, sync_source, job_id, mode, status,
                crate_id, crate_name, scope, track_count, created_at, started_at)
               VALUES (?, ?, ?, ?, ?, 'apply', 'running', ?, ?, ?, ?, ?, ?)""",
            (
                operation_id, operation_type, export_target, sync_source, job_id,
                crate_id, crate_name, scope, track_count, now, now,
            ),
        )
    return {"id": operation_id}


def finish_operation(
    operation_id: str,
    *,
    status: str,
    destination_relative: Optional[str] = None,
    result: Optional[str] = None,
    verification_status: Optional[str] = None,
    verification_details: Optional[list[str]] = None,
    warnings: Optional[list[str]] = None,
    error_reason: Optional[str] = None,
) -> None:
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"finish_operation requires a terminal status, got {status!r}")
    warnings_payload = json.dumps((warnings or [])[:_MAX_WARNINGS])
    details_payload = json.dumps((verification_details or [])[:_MAX_DETAILS])
    reason = error_reason[:_MAX_ERROR_REASON_LEN] if error_reason else None
    with get_conn() as conn:
        conn.execute(
            """UPDATE publish_operations
               SET status = ?, destination_relative = ?, result = ?,
                   verification_status = ?, verification_details_json = ?,
                   warnings_json = ?, error_reason = ?, finished_at = ?
               WHERE id = ?""",
            (
                status, destination_relative, result,
                verification_status, details_payload,
                warnings_payload, reason, _now(), operation_id,
            ),
        )


def get_operation(operation_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM publish_operations WHERE id = ?", (operation_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_recent(limit: int = 50, operation_type: Optional[str] = None) -> list[dict[str, Any]]:
    with get_conn() as conn:
        if operation_type:
            rows = conn.execute(
                "SELECT * FROM publish_operations WHERE operation_type = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (operation_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM publish_operations ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_dict(row) for row in rows]


def recover_interrupted_operations() -> int:
    """Close out operations left 'running' by a previous backend process.

    A restart must never silently resume a publish write, and a row must
    never be left permanently claiming 'running' after the process that
    owned it is gone.
    """
    now = _now()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM publish_operations WHERE status = 'running'"
        ).fetchall()
        for row in rows:
            conn.execute(
                """UPDATE publish_operations
                   SET status = 'failed', error_reason = ?, finished_at = ?
                   WHERE id = ?""",
                ("backend_restarted", now, row["id"]),
            )
    return len(rows)
