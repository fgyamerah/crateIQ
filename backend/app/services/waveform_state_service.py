"""Persistence helpers for W1 waveform state in backend-owned jobs.db."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..core.db import get_conn
from ..models.waveform import (
    ARTIFACT_TRANSITIONS,
    JOB_TRANSITIONS,
    WAVEFORM_ALGORITHM_VERSION,
    WAVEFORM_SCHEMA_VERSION,
    SourceStatSnapshot,
    WaveformArtifactStatus,
    WaveformJobRecord,
    WaveformJobStatus,
    WaveformTrackState,
)
from .track_source_service import library_identity


class InvalidWaveformTransition(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_from_row(row) -> WaveformTrackState:
    return WaveformTrackState(
        library_id=row["library_id"],
        track_id=row["track_id"],
        status=WaveformArtifactStatus(row["status"]),
        schema_version=row["schema_version"],
        algorithm_version=row["algorithm_version"],
        source_size_bytes=row["source_size_bytes"],
        source_mtime_ns=row["source_mtime_ns"],
        source_ctime_ns=row["source_ctime_ns"],
        source_device=row["source_device"],
        source_inode=row["source_inode"],
        source_sha256=row["source_sha256"],
        cache_key=row["cache_key"],
        generated_at=row["generated_at"],
        last_error_code=row["last_error_code"],
        updated_at=row["updated_at"],
    )


def get_track_state(track_id: int, *, library_id: str | None = None) -> WaveformTrackState:
    identity = library_id or library_identity()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM waveform_track_state WHERE library_id = ? AND track_id = ?",
            (identity, track_id),
        ).fetchone()
    if row:
        return _state_from_row(row)
    return WaveformTrackState(
        library_id=identity,
        track_id=track_id,
        status=WaveformArtifactStatus.NOT_GENERATED,
        schema_version=WAVEFORM_SCHEMA_VERSION,
        algorithm_version=WAVEFORM_ALGORITHM_VERSION,
    )


def ensure_track_state(track_id: int, *, library_id: str | None = None) -> WaveformTrackState:
    identity = library_id or library_identity()
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO waveform_track_state
               (library_id, track_id, status, schema_version, algorithm_version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                identity,
                track_id,
                WaveformArtifactStatus.NOT_GENERATED.value,
                WAVEFORM_SCHEMA_VERSION,
                WAVEFORM_ALGORITHM_VERSION,
                now,
            ),
        )
    return get_track_state(track_id, library_id=identity)


def transition_track_state(
    track_id: int,
    status: WaveformArtifactStatus | str,
    *,
    library_id: str | None = None,
    snapshot: SourceStatSnapshot | None = None,
    error_code: str | None = None,
) -> WaveformTrackState:
    identity = library_id or (snapshot.library_id if snapshot else library_identity())
    target = WaveformArtifactStatus(status)
    current = ensure_track_state(track_id, library_id=identity)
    if target != current.status and target not in ARTIFACT_TRANSITIONS[current.status]:
        raise InvalidWaveformTransition(f"invalid waveform transition: {current.status.value} -> {target.value}")
    if snapshot and (snapshot.library_id != identity or snapshot.track_id != track_id):
        raise ValueError("source stat snapshot identity does not match waveform state")

    now = _now()
    generated_at = now if target is WaveformArtifactStatus.READY else current.generated_at
    retained_error = (
        error_code or current.last_error_code
        if target in {WaveformArtifactStatus.FAILED, WaveformArtifactStatus.UNSUPPORTED}
        else None
    )
    with get_conn() as conn:
        conn.execute(
            """UPDATE waveform_track_state
               SET status = ?, source_size_bytes = ?, source_mtime_ns = ?,
                   source_ctime_ns = ?, source_device = ?, source_inode = ?,
                   generated_at = ?, last_error_code = ?, updated_at = ?
               WHERE library_id = ? AND track_id = ?""",
            (
                target.value,
                snapshot.source_size_bytes if snapshot else current.source_size_bytes,
                snapshot.source_mtime_ns if snapshot else current.source_mtime_ns,
                snapshot.source_ctime_ns if snapshot else current.source_ctime_ns,
                snapshot.source_device if snapshot else current.source_device,
                snapshot.source_inode if snapshot else current.source_inode,
                generated_at,
                retained_error,
                now,
                identity,
                track_id,
            ),
        )
    return get_track_state(track_id, library_id=identity)


def _job_from_row(row) -> WaveformJobRecord:
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


def create_waveform_job_record(track_id: int, *, library_id: str | None = None) -> WaveformJobRecord:
    """Persist one queued record only; W1 has no worker or automatic caller."""
    identity = library_id or library_identity()
    job_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO waveform_jobs
               (id, library_id, track_id, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (job_id, identity, track_id, WaveformJobStatus.QUEUED.value, _now()),
        )
    created = get_waveform_job(job_id)
    if created is None:  # pragma: no cover - the insert and read share one DB
        raise RuntimeError("waveform job record was not persisted")
    return created


def get_waveform_job(job_id: str) -> WaveformJobRecord | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM waveform_jobs WHERE id = ?", (job_id,)).fetchone()
    return _job_from_row(row) if row else None


def transition_waveform_job(
    job_id: str,
    status: WaveformJobStatus | str,
    *,
    error_code: str | None = None,
) -> WaveformJobRecord:
    current = get_waveform_job(job_id)
    if current is None:
        raise LookupError("waveform job not found")
    target = WaveformJobStatus(status)
    if target != current.status and target not in JOB_TRANSITIONS[current.status]:
        raise InvalidWaveformTransition(f"invalid waveform job transition: {current.status.value} -> {target.value}")
    now = _now()
    started_at = now if target is WaveformJobStatus.PROCESSING and not current.started_at else current.started_at
    finished_at = now if target in {WaveformJobStatus.SUCCEEDED, WaveformJobStatus.FAILED, WaveformJobStatus.CANCELLED} else current.finished_at
    with get_conn() as conn:
        conn.execute(
            """UPDATE waveform_jobs SET status = ?, started_at = ?, finished_at = ?, error_code = ?
               WHERE id = ?""",
            (target.value, started_at, finished_at, error_code, job_id),
        )
    updated = get_waveform_job(job_id)
    if updated is None:  # pragma: no cover - row existence was checked above
        raise RuntimeError("waveform job record disappeared")
    return updated


def request_waveform_job_cancellation(job_id: str) -> WaveformJobRecord:
    current = get_waveform_job(job_id)
    if current is None:
        raise LookupError("waveform job not found")
    with get_conn() as conn:
        conn.execute("UPDATE waveform_jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
    updated = get_waveform_job(job_id)
    if updated is None:  # pragma: no cover - row existence was checked above
        raise RuntimeError("waveform job record disappeared")
    return updated
