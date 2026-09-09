"""
job_service — CRUD operations for the jobs table.

All functions are synchronous (raw sqlite3).  The async layer in
toolkit_runner calls these directly; FastAPI route handlers call them
from async context, which is fine for short DB operations.

Functions never raise on "not found" — they return None so callers
can decide what HTTP status to return.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from ..core.db import get_conn
from ..core.library_key import current_library_key
from ..core.config import JOBS_LOG_DIR
from ..models.job import Job

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row) -> Job:
    return Job.from_row(row)


def _library_key(value: str | None) -> str:
    return value or current_library_key()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def create_job(command: str, args: List[str]) -> Job:
    """
    Insert a new job with status=pending and return the populated Job model.
    The log_path is assigned at creation time so it is available even before
    the subprocess starts.
    """
    job_id    = str(uuid.uuid4())
    library_key = current_library_key()
    log_path  = str(JOBS_LOG_DIR / library_key / f"{job_id}.log")
    now       = _now()
    args_json = json.dumps(args)

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, command, args_json, status, created_at, log_path, library_key)
               VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
            (job_id, command, args_json, now, log_path, library_key),
        )

    log.info("job=%s  created  command=%s  args=%s", job_id, command, args)
    return get_job(job_id, library_key=library_key)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_job(job_id: str, *, library_key: str | None = None) -> Optional[Job]:
    key = _library_key(library_key)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ? AND library_key = ?", (job_id, key)
        ).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(limit: int = 100, offset: int = 0) -> List[Job]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE library_key = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (current_library_key(), limit, offset),
        ).fetchall()
    return [_row_to_job(r) for r in rows]


# ---------------------------------------------------------------------------
# Status updates  (called by toolkit_runner)
# ---------------------------------------------------------------------------

def mark_running(job_id: str, *, library_key: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET status='running', started_at=? WHERE id=? AND library_key=?",
            (_now(), job_id, _library_key(library_key)),
        )


def mark_finished(
    job_id: str, status: str, exit_code: int, *, library_key: str | None = None
) -> None:
    """status must be 'succeeded', 'failed', or 'cancelled'."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE jobs
               SET status=?, finished_at=?, exit_code=?
               WHERE id=? AND library_key=?""",
            (status, _now(), exit_code, job_id, _library_key(library_key)),
        )


# ---------------------------------------------------------------------------
# Process / progress updates  (called by toolkit_runner / rsync_runner)
# ---------------------------------------------------------------------------

def mark_pid(job_id: str, pid: int, *, library_key: str | None = None) -> None:
    """Store the OS PID of the subprocess (called after proc is created)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET pid=? WHERE id=? AND library_key=?",
            (pid, job_id, _library_key(library_key)),
        )


def mark_progress(
    job_id:  str,
    current: int,
    total:   int,
    percent: float,
    message: str,
    *,
    library_key: str | None = None,
) -> None:
    """
    Update job progress fields.  Called from the rsync background task
    each time a parseable progress line is received.
    """
    with get_conn() as conn:
        conn.execute(
            """UPDATE jobs
               SET progress_current=?, progress_total=?,
                   progress_percent=?, progress_message=?
               WHERE id=? AND library_key=?""",
            (current, total, percent, message, job_id, _library_key(library_key)),
        )


def clear_pid(job_id: str, *, library_key: str | None = None) -> None:
    """Clear the PID once the process has exited."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET pid=NULL WHERE id=? AND library_key=?",
            (job_id, _library_key(library_key)),
        )
