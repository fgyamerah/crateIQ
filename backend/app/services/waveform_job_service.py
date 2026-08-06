"""Transactional waveform job lifecycle for the W3 generation workflow.

All operational state lives in backend-owned ``jobs.db``. The trusted
pipeline ``processed.db`` is never read or written here.

Concurrency contract: submission runs inside a ``BEGIN IMMEDIATE``
transaction and relies on W1's partial unique index
``idx_waveform_one_active_track`` so two simultaneous requests for the same
``(library_id, track_id)`` can never create two active jobs. Extraction
itself never runs inside a transaction — see :mod:`waveform_scheduler`.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from ..core.db import get_conn
from ..models.waveform import (
    WAVEFORM_ALGORITHM_VERSION,
    WAVEFORM_SCHEMA_VERSION,
    SourceStatSnapshot,
    WaveformArtifactStatus,
    WaveformJobRecord,
    WaveformJobStatus,
)

log = logging.getLogger(__name__)

ACTIVE_JOB_STATUSES = (WaveformJobStatus.QUEUED.value, WaveformJobStatus.PROCESSING.value)

ERROR_BACKEND_RESTARTED = "BACKEND_RESTARTED"
ERROR_BACKEND_SHUTDOWN = "BACKEND_SHUTDOWN"
ERROR_SUPERSEDED = "SUPERSEDED_BY_NEW_SOURCE_GENERATION"
ERROR_WORKER_FAILED = "WAVEFORM_WORKER_FAILED"
ERROR_CACHE_EVICTED = "WAVEFORM_CACHE_EVICTED"
ERROR_ARTIFACT_MISSING = "WAVEFORM_ARTIFACT_MISSING"
ERROR_CACHE_CLEARED = "WAVEFORM_CACHE_CLEARED"

# Minimum age before a ready track's access timestamp is rewritten. Waveform
# reads are infrequent, but a player can poll the same track repeatedly; this
# keeps LRU ordering meaningful without a DB write per request.
ACCESS_TOUCH_MIN_INTERVAL_SECONDS = 3600.0

# Operational-row retention, per the waveform ADR section 19. Only CrateIQ's
# own bookkeeping rows in jobs.db expire; the trusted pipeline DB is never
# touched and no cache artifact is deleted by these passes.
JOB_ROW_RETENTION_DAYS = 30
TRACK_STATE_RETENTION_DAYS = 30

# Track states safe to forget once they have gone quiet: none of them holds a
# cache artifact and none is part of an in-flight generation.
_EXPIRABLE_TRACK_STATUSES = (
    WaveformArtifactStatus.NOT_GENERATED.value,
    WaveformArtifactStatus.FAILED.value,
    WaveformArtifactStatus.CANCELLED.value,
    WaveformArtifactStatus.UNSUPPORTED.value,
    WaveformArtifactStatus.STALE.value,
)

SubmitOutcome = Literal["queued", "deduplicated", "already_ready", "queue_full"]


@dataclass(frozen=True)
class SubmitResult:
    outcome: SubmitOutcome
    job: WaveformJobRecord | None
    track_status: WaveformArtifactStatus

    @property
    def deduplicated(self) -> bool:
        return self.outcome == "deduplicated"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_from_row(row: sqlite3.Row) -> WaveformJobRecord:
    return WaveformJobRecord(
        id=row["id"],
        library_id=row["library_id"],
        track_id=row["track_id"],
        status=WaveformJobStatus(row["status"]),
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        cancel_requested=bool(row["cancel_requested"]),
        error_code=row["error_code"],
    )


def _active_job_row(conn: sqlite3.Connection, library_id: str, track_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM waveform_jobs "
        "WHERE library_id = ? AND track_id = ? AND status IN (?, ?)",
        (library_id, track_id, *ACTIVE_JOB_STATUSES),
    ).fetchone()


def get_job_generation_key(job_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT generation_key FROM waveform_jobs WHERE id = ?", (job_id,)).fetchone()
    return row["generation_key"] if row else None


def get_active_job_for_track(library_id: str, track_id: int) -> WaveformJobRecord | None:
    with get_conn() as conn:
        row = _active_job_row(conn, library_id, track_id)
    return _job_from_row(row) if row else None


def count_queued_jobs() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM waveform_jobs WHERE status = ?",
            (WaveformJobStatus.QUEUED.value,),
        ).fetchone()
    return int(row["n"])


def submit_generation_job(
    *,
    snapshot: SourceStatSnapshot,
    generation_key: str,
    force: bool,
    max_queue_size: int,
) -> SubmitResult:
    """Create or reuse exactly one active generation job.

    Deduplication rules:

    * an active job with the *same* generation key is reused, even when
      ``force=true`` — repeated POSTs can never launch a second decoder;
    * an active job with a *different* generation key means the source
      changed underneath it, so it is superseded (cancelled) and replaced;
    * ``force=false`` with a ready artifact for this exact generation key
      short-circuits without queueing anything.

    A ready track is deliberately left in ``ready`` state while a forced
    regeneration runs, so the previous valid waveform stays readable until
    the replacement is atomically published.
    """
    library_id = snapshot.library_id
    track_id = snapshot.track_id
    now = _now()

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")

        state_row = conn.execute(
            "SELECT * FROM waveform_track_state WHERE library_id = ? AND track_id = ?",
            (library_id, track_id),
        ).fetchone()
        current_status = (
            WaveformArtifactStatus(state_row["status"]) if state_row else WaveformArtifactStatus.NOT_GENERATED
        )

        existing = _active_job_row(conn, library_id, track_id)
        if existing is not None:
            if existing["generation_key"] == generation_key:
                return SubmitResult("deduplicated", _job_from_row(existing), current_status)
            conn.execute(
                "UPDATE waveform_jobs SET status = ?, finished_at = ?, error_code = ? WHERE id = ?",
                (WaveformJobStatus.CANCELLED.value, now, ERROR_SUPERSEDED, existing["id"]),
            )

        if (
            not force
            and current_status is WaveformArtifactStatus.READY
            and state_row is not None
            and state_row["cache_key"] == generation_key
        ):
            return SubmitResult("already_ready", None, current_status)

        if count_queued_in(conn) >= max_queue_size:
            return SubmitResult("queue_full", None, current_status)

        job_id = str(uuid.uuid4())
        try:
            conn.execute(
                """INSERT INTO waveform_jobs
                   (id, library_id, track_id, status, created_at, generation_key)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (job_id, library_id, track_id, WaveformJobStatus.QUEUED.value, now, generation_key),
            )
        except sqlite3.IntegrityError:
            # The partial unique index won a concurrent race; reuse the winner.
            winner = _active_job_row(conn, library_id, track_id)
            if winner is None:
                raise
            return SubmitResult("deduplicated", _job_from_row(winner), current_status)

        _upsert_state_for_submission(conn, state_row, snapshot, current_status, now)
        created = conn.execute("SELECT * FROM waveform_jobs WHERE id = ?", (job_id,)).fetchone()
        return SubmitResult("queued", _job_from_row(created), current_status)


def count_queued_in(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM waveform_jobs WHERE status = ?",
        (WaveformJobStatus.QUEUED.value,),
    ).fetchone()
    return int(row["n"])


def _upsert_state_for_submission(
    conn: sqlite3.Connection,
    state_row: sqlite3.Row | None,
    snapshot: SourceStatSnapshot,
    current_status: WaveformArtifactStatus,
    now: str,
) -> None:
    """Move the track toward ``queued`` unless a valid ready artifact exists.

    A ready track keeps its ready state during forced regeneration so the
    existing artifact remains servable until replacement is published.
    """
    if state_row is None:
        conn.execute(
            """INSERT INTO waveform_track_state
               (library_id, track_id, status, schema_version, algorithm_version,
                source_size_bytes, source_mtime_ns, source_ctime_ns,
                source_device, source_inode, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.library_id,
                snapshot.track_id,
                WaveformArtifactStatus.QUEUED.value,
                WAVEFORM_SCHEMA_VERSION,
                WAVEFORM_ALGORITHM_VERSION,
                snapshot.source_size_bytes,
                snapshot.source_mtime_ns,
                snapshot.source_ctime_ns,
                snapshot.source_device,
                snapshot.source_inode,
                now,
            ),
        )
        return

    if current_status is WaveformArtifactStatus.READY:
        return  # keep the previous valid artifact visible during regeneration

    conn.execute(
        """UPDATE waveform_track_state
           SET status = ?, source_size_bytes = ?, source_mtime_ns = ?,
               source_ctime_ns = ?, source_device = ?, source_inode = ?,
               last_error_code = NULL, updated_at = ?
           WHERE library_id = ? AND track_id = ?""",
        (
            WaveformArtifactStatus.QUEUED.value,
            snapshot.source_size_bytes,
            snapshot.source_mtime_ns,
            snapshot.source_ctime_ns,
            snapshot.source_device,
            snapshot.source_inode,
            now,
            snapshot.library_id,
            snapshot.track_id,
        ),
    )


def claim_job(job_id: str) -> WaveformJobRecord | None:
    """Transition a queued job to processing. Returns ``None`` if not claimable."""
    now = _now()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM waveform_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None or row["status"] != WaveformJobStatus.QUEUED.value:
            return None
        if row["cancel_requested"]:
            return None
        conn.execute(
            "UPDATE waveform_jobs SET status = ?, started_at = ? WHERE id = ?",
            (WaveformJobStatus.PROCESSING.value, now, job_id),
        )
        conn.execute(
            """UPDATE waveform_track_state SET status = ?, updated_at = ?
               WHERE library_id = ? AND track_id = ? AND status = ?""",
            (
                WaveformArtifactStatus.PROCESSING.value,
                now,
                row["library_id"],
                row["track_id"],
                WaveformArtifactStatus.QUEUED.value,
            ),
        )
        claimed = conn.execute("SELECT * FROM waveform_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job_from_row(claimed)


def is_cancel_requested(job_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT cancel_requested FROM waveform_jobs WHERE id = ?", (job_id,)).fetchone()
    return bool(row["cancel_requested"]) if row else False


def complete_job_ready(job_id: str, *, generation_key: str, snapshot: SourceStatSnapshot) -> None:
    """Mark the artifact ready and the job succeeded in one short transaction.

    Called only after the artifact has been atomically published, so a ready
    state can never become visible before its cache file exists.
    """
    now = _now()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """UPDATE waveform_track_state
               SET status = ?, cache_key = ?, generated_at = ?, last_error_code = NULL,
                   source_size_bytes = ?, source_mtime_ns = ?, source_ctime_ns = ?,
                   source_device = ?, source_inode = ?, updated_at = ?
               WHERE library_id = ? AND track_id = ?""",
            (
                WaveformArtifactStatus.READY.value,
                generation_key,
                now,
                snapshot.source_size_bytes,
                snapshot.source_mtime_ns,
                snapshot.source_ctime_ns,
                snapshot.source_device,
                snapshot.source_inode,
                now,
                snapshot.library_id,
                snapshot.track_id,
            ),
        )
        conn.execute(
            "UPDATE waveform_jobs SET status = ?, finished_at = ?, error_code = NULL WHERE id = ?",
            (WaveformJobStatus.SUCCEEDED.value, now, job_id),
        )


def finish_job_unsuccessfully(
    job_id: str,
    *,
    job_status: WaveformJobStatus,
    track_status: WaveformArtifactStatus | None,
    error_code: str | None,
) -> None:
    """Terminate a job without publishing, leaving any prior artifact intact.

    ``track_status`` is applied only when the track is still queued or
    processing, so a previously ready waveform survives a failed or
    cancelled regeneration.
    """
    now = _now()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM waveform_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return
        conn.execute(
            "UPDATE waveform_jobs SET status = ?, finished_at = ?, error_code = ? WHERE id = ?",
            (job_status.value, now, error_code, job_id),
        )
        if track_status is not None:
            conn.execute(
                """UPDATE waveform_track_state
                   SET status = ?, last_error_code = ?, updated_at = ?
                   WHERE library_id = ? AND track_id = ? AND status IN (?, ?)""",
                (
                    track_status.value,
                    error_code,
                    now,
                    row["library_id"],
                    row["track_id"],
                    WaveformArtifactStatus.QUEUED.value,
                    WaveformArtifactStatus.PROCESSING.value,
                ),
            )


def request_cancellation(job_id: str) -> WaveformJobRecord | None:
    """Flag a job for cancellation. Terminal jobs are left untouched."""
    now = _now()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM waveform_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        if row["status"] not in ACTIVE_JOB_STATUSES:
            return _job_from_row(row)  # already finished: deterministic no-op
        conn.execute("UPDATE waveform_jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
        if row["status"] == WaveformJobStatus.QUEUED.value:
            # A queued job has no worker to interrupt; finish it here so the
            # active-job index is released immediately.
            conn.execute(
                "UPDATE waveform_jobs SET status = ?, finished_at = ? WHERE id = ?",
                (WaveformJobStatus.CANCELLED.value, now, job_id),
            )
            conn.execute(
                """UPDATE waveform_track_state SET status = ?, updated_at = ?
                   WHERE library_id = ? AND track_id = ? AND status IN (?, ?)""",
                (
                    WaveformArtifactStatus.CANCELLED.value,
                    now,
                    row["library_id"],
                    row["track_id"],
                    WaveformArtifactStatus.QUEUED.value,
                    WaveformArtifactStatus.PROCESSING.value,
                ),
            )
        updated = conn.execute("SELECT * FROM waveform_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job_from_row(updated)


def get_job(job_id: str) -> WaveformJobRecord | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM waveform_jobs WHERE id = ?", (job_id,)).fetchone()
    return _job_from_row(row) if row else None


# ---------------------------------------------------------------------------
# W6 cache-eviction support
#
# These helpers give the cache service the operational metadata it needs to
# order eviction and to keep jobs.db consistent when an artifact is removed.
# They never touch source audio and never read the trusted pipeline DB.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CachedArtifactRef:
    """One ready cache artifact as jobs.db currently understands it."""

    library_id: str
    track_id: int
    generation_key: str
    last_used_at: str
    status: str


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def touch_artifact_access(library_id: str, track_id: int) -> bool:
    """Record that a ready artifact was served. Returns True when written.

    Rate-limited by ``ACCESS_TOUCH_MIN_INTERVAL_SECONDS`` so repeated reads of
    the same track do not amplify into a write per request.
    """
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_accessed_at, generated_at FROM waveform_track_state "
            "WHERE library_id = ? AND track_id = ?",
            (library_id, track_id),
        ).fetchone()
        if row is None:
            return False
        previous = _parse_timestamp(row["last_accessed_at"]) or _parse_timestamp(row["generated_at"])
        if previous is not None and (now - previous).total_seconds() < ACCESS_TOUCH_MIN_INTERVAL_SECONDS:
            return False
        conn.execute(
            "UPDATE waveform_track_state SET last_accessed_at = ? WHERE library_id = ? AND track_id = ?",
            (now.isoformat(), library_id, track_id),
        )
    return True


def list_referenced_generation_keys() -> set[str]:
    """Every generation key any track state currently points at, ready or not."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT cache_key FROM waveform_track_state WHERE cache_key IS NOT NULL"
        ).fetchall()
    return {row["cache_key"] for row in rows if row["cache_key"]}


def list_cached_artifacts() -> list[CachedArtifactRef]:
    """Return referenced artifacts ordered least-recently-used first.

    Ordering key is ``last_accessed_at`` when present, else ``generated_at``,
    else ``updated_at`` — so an artifact that has never been read still has a
    deterministic position rather than being treated as infinitely recent.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT library_id, track_id, cache_key, status,
                      COALESCE(last_accessed_at, generated_at, updated_at) AS last_used_at
               FROM waveform_track_state
               WHERE cache_key IS NOT NULL
               ORDER BY last_used_at ASC"""
        ).fetchall()
    return [
        CachedArtifactRef(
            library_id=row["library_id"],
            track_id=row["track_id"],
            generation_key=row["cache_key"],
            last_used_at=row["last_used_at"] or "",
            status=row["status"],
        )
        for row in rows
    ]


def mark_artifact_unavailable(
    library_id: str,
    track_id: int,
    *,
    error_code: str = ERROR_CACHE_EVICTED,
) -> None:
    """Drop a track's claim on a cache artifact that no longer exists.

    Called when W6 evicts or reconciles away an artifact so jobs.db can never
    keep advertising ``ready`` for a file that is gone. The track becomes
    ``stale``; nothing is regenerated automatically.
    """
    now = _now()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """UPDATE waveform_track_state
               SET status = ?, cache_key = NULL, generated_at = NULL,
                   last_error_code = ?, updated_at = ?
               WHERE library_id = ? AND track_id = ? AND status = ?""",
            (
                WaveformArtifactStatus.STALE.value,
                error_code,
                now,
                library_id,
                track_id,
                WaveformArtifactStatus.READY.value,
            ),
        )


def list_ready_states() -> list[CachedArtifactRef]:
    """Only tracks currently advertising a ready artifact."""
    return [ref for ref in list_cached_artifacts() if ref.status == WaveformArtifactStatus.READY.value]


def fail_active_jobs_for_shutdown() -> int:
    """Close out jobs still active when the backend is shutting down.

    Distinct from a user cancellation: the operator did not cancel anything,
    the process stopped. Nothing is resumed on the next start.
    """
    now = _now()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, library_id, track_id FROM waveform_jobs WHERE status IN (?, ?)",
            ACTIVE_JOB_STATUSES,
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE waveform_jobs SET status = ?, finished_at = ?, error_code = ? WHERE id = ?",
                (WaveformJobStatus.FAILED.value, now, ERROR_BACKEND_SHUTDOWN, row["id"]),
            )
            conn.execute(
                """UPDATE waveform_track_state
                   SET status = ?, last_error_code = ?, updated_at = ?
                   WHERE library_id = ? AND track_id = ? AND status IN (?, ?)""",
                (
                    WaveformArtifactStatus.FAILED.value,
                    ERROR_BACKEND_SHUTDOWN,
                    now,
                    row["library_id"],
                    row["track_id"],
                    WaveformArtifactStatus.QUEUED.value,
                    WaveformArtifactStatus.PROCESSING.value,
                ),
            )
    if rows:
        log.info("waveform shutdown closed active_jobs=%d", len(rows))
    return len(rows)


def purge_expired_job_rows(retention_days: int = JOB_ROW_RETENTION_DAYS) -> int:
    """Delete aged-out failed/cancelled job rows. Rows only, never artifacts.

    Succeeded rows are kept: they are the record of what produced a cache
    artifact that may still be on disk. A job row carries no path, no source
    identity, and no audio, so removing it loses nothing but history.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """DELETE FROM waveform_jobs
               WHERE status IN (?, ?)
                 AND finished_at IS NOT NULL
                 AND finished_at < ?""",
            (WaveformJobStatus.FAILED.value, WaveformJobStatus.CANCELLED.value, cutoff),
        )
        deleted = cursor.rowcount or 0
    if deleted:
        log.info("waveform job row retention deleted_rows=%d retention_days=%d", deleted, retention_days)
    return deleted


def purge_expired_track_states(retention_days: int = TRACK_STATE_RETENTION_DAYS) -> int:
    """Forget track states that hold no artifact and have gone quiet.

    Guarded so a row is only removed when it cannot matter: it must hold no
    ``cache_key``, be in a non-active, non-ready status, and have had no
    update or access within the retention window. A forgotten track simply
    reads back as ``not_generated``, which is also its state before any
    waveform is ever requested.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    placeholders = ", ".join("?" for _ in _EXPIRABLE_TRACK_STATUSES)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            f"""DELETE FROM waveform_track_state
                WHERE cache_key IS NULL
                  AND status IN ({placeholders})
                  AND COALESCE(last_accessed_at, updated_at) < ?""",
            (*_EXPIRABLE_TRACK_STATUSES, cutoff),
        )
        deleted = cursor.rowcount or 0
    if deleted:
        log.info("waveform track state retention deleted_rows=%d retention_days=%d", deleted, retention_days)
    return deleted


def recover_interrupted_jobs() -> int:
    """Terminate jobs left active by a previous process.

    A backend restart must never silently resume music-library analysis, so
    interrupted ``processing`` jobs and leftover ``queued`` jobs are both
    closed out. Regeneration requires a renewed explicit request. This only
    rewrites operational rows; no audio is read.
    """
    now = _now()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, library_id, track_id, status FROM waveform_jobs WHERE status IN (?, ?)",
            ACTIVE_JOB_STATUSES,
        ).fetchall()
        for row in rows:
            interrupted = row["status"] == WaveformJobStatus.PROCESSING.value
            job_status = WaveformJobStatus.FAILED if interrupted else WaveformJobStatus.CANCELLED
            track_status = (
                WaveformArtifactStatus.FAILED if interrupted else WaveformArtifactStatus.CANCELLED
            )
            conn.execute(
                "UPDATE waveform_jobs SET status = ?, finished_at = ?, error_code = ? WHERE id = ?",
                (job_status.value, now, ERROR_BACKEND_RESTARTED, row["id"]),
            )
            conn.execute(
                """UPDATE waveform_track_state
                   SET status = ?, last_error_code = ?, updated_at = ?
                   WHERE library_id = ? AND track_id = ? AND status IN (?, ?)""",
                (
                    track_status.value,
                    ERROR_BACKEND_RESTARTED,
                    now,
                    row["library_id"],
                    row["track_id"],
                    WaveformArtifactStatus.QUEUED.value,
                    WaveformArtifactStatus.PROCESSING.value,
                ),
            )
    if rows:
        log.info("waveform recovery closed interrupted_jobs=%d", len(rows))
    return len(rows)
