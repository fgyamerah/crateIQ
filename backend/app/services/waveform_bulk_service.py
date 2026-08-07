"""Bulk 'Generate missing waveforms' workflow (Waveform Jobs Stage 2).

This module adds only a parent/batch orchestration layer on top of the
existing single-track generation pipeline -- it never creates a second
generation path. A read-only preview counts tracks by their current
``waveform_track_state`` status; the bulk feeder submits one eligible track
at a time through the exact same call sequence a single explicit POST uses
(:mod:`waveform_job_service` + :class:`WaveformScheduler`), and waits for
that job to reach a terminal state before submitting the next one. This
keeps the existing scheduler's bounded queue and concurrency authoritative:
bulk generation can never enqueue more than one job at a time on its own
behalf, regardless of how many tracks are eligible.

The feeder runs as a fire-and-forget ``asyncio`` background task so the
triggering POST returns immediately (202); progress is polled through the
``waveform_operations`` history table via :mod:`waveform_operations_service`.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from ..models.waveform import WaveformArtifactStatus, WaveformJobStatus
from . import (
    track_source_service,
    waveform_identity,
    waveform_job_service,
    waveform_operations_service,
    waveform_state_service,
)
from .waveform_readiness_service import WaveformRuntimeError, generation_blocker, resolve_cache_runtime
from .waveform_scheduler import get_scheduler

log = logging.getLogger(__name__)

# How often the bulk feeder polls a submitted job for a terminal state. The
# scheduler itself has no push notification, so this mirrors the frontend's
# own job-status poll cadence rather than inventing a new mechanism.
_JOB_POLL_INTERVAL_SECONDS = 0.5

_ACTIVE_STATUSES = (WaveformArtifactStatus.QUEUED.value, WaveformArtifactStatus.PROCESSING.value)
_TERMINAL_JOB_STATUSES = (WaveformJobStatus.SUCCEEDED, WaveformJobStatus.FAILED, WaveformJobStatus.CANCELLED)

# Registry so a fire-and-forget bulk task can never be garbage-collected
# mid-run (the same pattern rsync_runner.start_sync_job uses).
_running_tasks: set[asyncio.Task] = set()


def _all_track_ids() -> list[int]:
    """Every track id in the currently selected library, cheapest possible read."""
    root = selected_library_root()
    db_path = assert_path_under_root(library_db_path(root), root)
    if not db_path.is_file():
        return []
    with sqlite3.connect(db_path) as conn:
        return [row[0] for row in conn.execute("SELECT id FROM tracks ORDER BY id")]


def _states_by_track(library_id: str) -> dict[int, str]:
    from ..core.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT track_id, status FROM waveform_track_state WHERE library_id = ?",
            (library_id,),
        ).fetchall()
    return {row["track_id"]: row["status"] for row in rows}


def _is_eligible(status: str) -> bool:
    """A track needs generation: no ready artifact, not already active, not
    a permanently unsupported format."""
    return status not in _ACTIVE_STATUSES and status not in (
        WaveformArtifactStatus.READY.value,
        WaveformArtifactStatus.UNSUPPORTED.value,
    )


def _counts(track_ids: list[int], states: dict[int, str]) -> dict[str, Any]:
    ready = generating = failed = unsupported = missing = 0
    for track_id in track_ids:
        status = states.get(track_id, WaveformArtifactStatus.NOT_GENERATED.value)
        if status == WaveformArtifactStatus.READY.value:
            ready += 1
        elif status in _ACTIVE_STATUSES:
            generating += 1
        else:
            if status == WaveformArtifactStatus.UNSUPPORTED.value:
                unsupported += 1
            else:
                missing += 1
                if status == WaveformArtifactStatus.FAILED.value:
                    failed += 1
    return {
        "total_tracks": len(track_ids),
        "ready": ready,
        "generating": generating,
        "failed": failed,
        "unsupported": unsupported,
        "missing": missing,
        "eligible_to_generate": missing,
    }


def preview_missing() -> dict[str, Any]:
    """Read-only counts. Never enqueues a job, runs FFmpeg, or writes any state."""
    library_id = track_source_service.library_identity()
    track_ids = _all_track_ids()
    states = _states_by_track(library_id)
    return _counts(track_ids, states)


def _eligible_track_ids(track_ids: list[int], states: dict[int, str]) -> list[int]:
    return [
        track_id for track_id in track_ids
        if _is_eligible(states.get(track_id, WaveformArtifactStatus.NOT_GENERATED.value))
    ]


def _remaining_missing_count(library_id: str) -> int:
    track_ids = _all_track_ids()
    states = _states_by_track(library_id)
    return len(_eligible_track_ids(track_ids, states))


def start_generate_missing() -> dict[str, Any]:
    """Create a running bulk operation and fire its incremental feeder.

    Returns immediately with the operation id; the caller polls
    ``GET /waveform-bulk/operations/{id}`` for progress. Candidates are
    fixed at the eligible set observed right now -- a track that becomes
    eligible only after this call started is left for the next explicit run.
    """
    library_id = track_source_service.library_identity()
    track_ids = _all_track_ids()
    states = _states_by_track(library_id)
    eligible = _eligible_track_ids(track_ids, states)

    operation = waveform_operations_service.start_operation(
        total_tracks=len(track_ids), eligible_total=len(eligible),
    )
    operation_id = operation["id"]
    task = asyncio.create_task(_run_generate_missing(operation_id, eligible, library_id))
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)
    return {"id": operation_id, "total_tracks": len(track_ids), "eligible_total": len(eligible)}


async def _submit_and_await(track_id: int, scheduler) -> str:
    """Submit one track through the same path a single explicit POST uses,
    then wait for its job to reach a terminal state.

    Returns 'generated', 'skipped', or 'failed'. Never raises -- every
    failure mode this can hit (source unavailable, queue full, worker
    failure) is a truthful per-track outcome, not a reason to abort the
    whole batch.
    """
    try:
        snapshot = track_source_service.source_stat_snapshot(track_id)
    except (LookupError, ValueError, OSError):
        return "failed"

    generation_key = waveform_identity.compute_generation_key(snapshot)
    result = waveform_job_service.submit_generation_job(
        snapshot=snapshot,
        generation_key=generation_key,
        force=False,
        max_queue_size=scheduler.max_queue_size,
    )
    if result.outcome == "already_ready":
        # Another explicit action (e.g. a manual single-track request)
        # finished this track between our preview read and reaching it here.
        return "skipped"
    if result.outcome == "queue_full":
        return "failed"

    job = result.job
    assert job is not None  # queued/deduplicated always carry a job

    if result.outcome == "queued" and not scheduler.enqueue(job.id):
        waveform_job_service.finish_job_unsuccessfully(
            job.id,
            job_status=waveform_job_service.WaveformJobStatus.FAILED,
            track_status=WaveformArtifactStatus.FAILED,
            error_code="WAVEFORM_QUEUE_FULL",
        )
        return "failed"
    # 'deduplicated' means an active job for this exact generation already
    # exists (started by this feeder or another caller); just observe it.

    current = waveform_job_service.get_job(job.id)
    while current is not None and current.status not in _TERMINAL_JOB_STATUSES:
        await asyncio.sleep(_JOB_POLL_INTERVAL_SECONDS)
        current = waveform_job_service.get_job(job.id)

    if current is None:
        return "failed"
    if current.status is WaveformJobStatus.SUCCEEDED:
        return "generated"
    if current.status is WaveformJobStatus.CANCELLED:
        return "skipped"
    return "failed"


async def _run_generate_missing(operation_id: str, candidates: list[int], library_id: str) -> None:
    processed = generated = skipped = failed = 0
    cancelled = False

    try:
        _config, validated_cache = resolve_cache_runtime()
    except WaveformRuntimeError as exc:
        waveform_operations_service.finish_operation(
            operation_id, status="failed", processed=0, generated=0, skipped=0, failed=0,
            remaining_missing=len(candidates), error_reason=exc.code,
        )
        return
    blocker = generation_blocker(validated_cache)
    if blocker is not None:
        waveform_operations_service.finish_operation(
            operation_id, status="failed", processed=0, generated=0, skipped=0, failed=0,
            remaining_missing=len(candidates), error_reason=blocker.code,
        )
        return

    try:
        scheduler = get_scheduler()
        for track_id in candidates:
            if waveform_operations_service.is_cancel_requested(operation_id):
                cancelled = True
                break

            # Re-check right before submitting: a concurrent single-track
            # request or an earlier step in this same run may have already
            # resolved this track since the batch was scoped.
            state = waveform_state_service.get_track_state(track_id, library_id=library_id)
            if not _is_eligible(state.status.value):
                skipped += 1
            else:
                outcome = await _submit_and_await(track_id, scheduler)
                if outcome == "generated":
                    generated += 1
                elif outcome == "skipped":
                    skipped += 1
                else:
                    failed += 1
            processed += 1
            waveform_operations_service.update_progress(
                operation_id, processed=processed, generated=generated, skipped=skipped, failed=failed,
            )
    except Exception as exc:  # pragma: no cover - a bulk run must never crash the process
        log.exception("bulk waveform generation failed operation_id=%s", operation_id)
        waveform_operations_service.finish_operation(
            operation_id, status="failed", processed=processed, generated=generated,
            skipped=skipped, failed=failed, remaining_missing=None, error_reason=str(exc),
        )
        return

    remaining = _remaining_missing_count(library_id)
    waveform_operations_service.finish_operation(
        operation_id,
        status="cancelled" if cancelled else "completed",
        processed=processed, generated=generated, skipped=skipped, failed=failed,
        remaining_missing=remaining,
    )
    log.info(
        "bulk waveform generation finished operation_id=%s status=%s processed=%d generated=%d skipped=%d failed=%d",
        operation_id, "cancelled" if cancelled else "completed", processed, generated, skipped, failed,
    )


def operation_detail(operation_id: str) -> dict[str, Any]:
    record = waveform_operations_service.get_operation(operation_id)
    if record is None:
        raise ValueError(f"Waveform bulk operation {operation_id} not found.")
    return record


def cancel_operation(operation_id: str) -> dict[str, Any]:
    """Idempotently request cancellation. Stops scheduling new tracks; the
    track currently in flight is left to finish so its result is truthful."""
    record = waveform_operations_service.request_cancel(operation_id)
    if record is None:
        raise ValueError(f"Waveform bulk operation {operation_id} not found.")
    return record


def history(limit: int = 50) -> dict[str, Any]:
    return {"history": waveform_operations_service.list_recent(limit=limit)}
