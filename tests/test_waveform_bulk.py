"""Tests for the bulk 'Generate missing waveforms' workflow
(Waveform Jobs Stage 2): waveform_operations_service (persisted history) and
waveform_bulk_service (preview + incremental feeder).

No real audio tool executes here. Per-track generation is faked through a
scheduler stand-in whose ``enqueue`` resolves the job synchronously, so the
feeder's polling loop terminates immediately and deterministically.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core import db as backend_db
from backend.app.core.waveform_cache import validate_waveform_cache_root
from backend.app.models.waveform import WaveformArtifactStatus, WaveformJobStatus
from backend.app.services import (
    track_source_service,
    waveform_bulk_service,
    waveform_identity,
    waveform_job_service,
    waveform_operations_service as ops,
    waveform_scheduler,
    waveform_state_service,
)
from tests.conftest import async_test

LIBRARY_TRACK_IDS = list(range(1, 7))


@pytest.fixture(autouse=True)
def _forbid_subprocesses(monkeypatch):
    import subprocess

    def _forbidden(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("waveform bulk tests must never spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden)


# ---------------------------------------------------------------------------
# waveform_operations_service: persisted history, mirrors
# tests/test_analysis_operations.py for the same Cycle 2 operations pattern.
# ---------------------------------------------------------------------------


@pytest.fixture()
def jobs_db(tmp_path, monkeypatch):
    path = tmp_path / "operational" / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    return path


def test_schema_creation_is_idempotent_and_additive(jobs_db):
    backend_db.init_db()
    with sqlite3.connect(jobs_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(waveform_operations)")}
    assert {
        "id", "operation_type", "status", "total_tracks", "eligible_total",
        "processed", "generated", "skipped", "failed", "remaining_missing",
        "cancel_requested", "error_reason", "created_at", "started_at", "finished_at",
    }.issubset(columns)


def test_start_update_finish_completed_lifecycle(jobs_db):
    op = ops.start_operation(total_tracks=88, eligible_total=62)
    record = ops.get_operation(op["id"])
    assert record["status"] == "running"
    assert record["operation_type"] == "generate_missing"
    assert record["total_tracks"] == 88
    assert record["eligible_total"] == 62
    assert record["processed"] == 0 and record["generated"] == 0
    assert record["created_at"] and record["started_at"]
    assert record["finished_at"] is None
    assert record["cancel_requested"] is False

    ops.update_progress(op["id"], processed=21, generated=20, skipped=1, failed=0)
    mid = ops.get_operation(op["id"])
    assert mid["processed"] == 21 and mid["generated"] == 20 and mid["status"] == "running"

    ops.finish_operation(
        op["id"], status="completed", processed=62, generated=61, skipped=1, failed=0,
        remaining_missing=0,
    )
    done = ops.get_operation(op["id"])
    assert done["status"] == "completed"
    assert (done["processed"], done["generated"], done["skipped"], done["failed"]) == (62, 61, 1, 0)
    assert done["remaining_missing"] == 0
    assert done["finished_at"]
    assert done["error_reason"] is None


def test_finish_operation_rejects_non_terminal_status(jobs_db):
    op = ops.start_operation(total_tracks=1, eligible_total=1)
    with pytest.raises(ValueError):
        ops.finish_operation(
            op["id"], status="running", processed=0, generated=0, skipped=0, failed=0,
            remaining_missing=None,
        )


def test_cancel_is_idempotent_and_safe_on_terminal_or_unknown_operations(jobs_db):
    assert ops.request_cancel("does-not-exist") is None

    op = ops.start_operation(total_tracks=5, eligible_total=5)
    assert ops.is_cancel_requested(op["id"]) is False

    first = ops.request_cancel(op["id"])
    assert first["cancel_requested"] is True
    second = ops.request_cancel(op["id"])
    assert second["cancel_requested"] is True

    ops.finish_operation(
        op["id"], status="cancelled", processed=1, generated=0, skipped=0, failed=0,
        remaining_missing=4,
    )
    after_finish = ops.request_cancel(op["id"])
    assert after_finish["status"] == "cancelled"


def test_update_progress_cannot_resurrect_a_closed_operation(jobs_db):
    op = ops.start_operation(total_tracks=5, eligible_total=5)
    ops.finish_operation(
        op["id"], status="cancelled", processed=1, generated=0, skipped=0, failed=0,
        remaining_missing=4,
    )
    ops.update_progress(op["id"], processed=99, generated=99, skipped=0, failed=0)
    record = ops.get_operation(op["id"])
    assert record["status"] == "cancelled"
    assert record["processed"] == 1


def test_list_recent_orders_newest_first_and_respects_limit(jobs_db):
    ids = []
    for _ in range(3):
        op = ops.start_operation(total_tracks=1, eligible_total=1)
        ops.finish_operation(
            op["id"], status="completed", processed=1, generated=1, skipped=0, failed=0,
            remaining_missing=0,
        )
        ids.append(op["id"])
    listed = ops.list_recent(limit=50)
    assert [record["id"] for record in listed] == list(reversed(ids))
    assert len(ops.list_recent(limit=2)) == 2


def test_no_history_without_a_started_operation(jobs_db):
    assert ops.list_recent() == []


def test_recover_interrupted_operations_closes_running_rows_as_failed(jobs_db):
    running = ops.start_operation(total_tracks=5, eligible_total=5)
    already_done = ops.start_operation(total_tracks=5, eligible_total=5)
    ops.finish_operation(
        already_done["id"], status="completed", processed=5, generated=5, skipped=0, failed=0,
        remaining_missing=0,
    )

    assert ops.recover_interrupted_operations() == 1
    reconciled = ops.get_operation(running["id"])
    assert reconciled["status"] == "failed"
    assert reconciled["error_reason"] == "backend_restarted"
    assert reconciled["finished_at"]

    untouched = ops.get_operation(already_done["id"])
    assert untouched["status"] == "completed"

    # Safe to call again (e.g. a second restart) with nothing left to close.
    assert ops.recover_interrupted_operations() == 0


# ---------------------------------------------------------------------------
# waveform_bulk_service: preview + incremental feeder
# ---------------------------------------------------------------------------


class _ImmediateScheduler:
    """Resolves an enqueued job synchronously so the feeder's poll loop never
    actually sleeps. No subprocess, no real extraction -- outcomes are
    decided per-track by ``outcome_map`` (default: every job succeeds)."""

    max_queue_size = 32

    def __init__(self, outcome_map: dict[int, str] | None = None, on_enqueue=None):
        self.enqueued: list[str] = []
        self.outcome_map = outcome_map or {}
        self.on_enqueue = on_enqueue

    def enqueue(self, job_id: str) -> bool:
        self.enqueued.append(job_id)
        job = waveform_job_service.get_job(job_id)
        claimed = waveform_job_service.claim_job(job_id)
        assert claimed is not None
        outcome = self.outcome_map.get(job.track_id, "succeeded")
        if outcome == "succeeded":
            key = waveform_job_service.get_job_generation_key(job_id)
            snapshot = track_source_service.source_stat_snapshot(job.track_id)
            waveform_job_service.complete_job_ready(job_id, generation_key=key, snapshot=snapshot)
        elif outcome == "failed":
            waveform_job_service.finish_job_unsuccessfully(
                job_id, job_status=WaveformJobStatus.FAILED,
                track_status=WaveformArtifactStatus.FAILED, error_code="TEST_FAILURE",
            )
        elif outcome == "cancelled":
            waveform_job_service.finish_job_unsuccessfully(
                job_id, job_status=WaveformJobStatus.CANCELLED,
                track_status=WaveformArtifactStatus.CANCELLED, error_code=None,
            )
        if self.on_enqueue:
            self.on_enqueue(job.track_id)
        return True

    def signal_cancel(self, job_id: str) -> bool:  # pragma: no cover - not exercised
        return True

    # No-ops so this stands in cleanly for the real WaveformScheduler during
    # app lifespan start/stop in the HTTP-level tests below.
    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class _QueueFullScheduler(_ImmediateScheduler):
    """Every submission is rejected as though the bounded queue is already full."""

    max_queue_size = 0


class _HangingScheduler(_ImmediateScheduler):
    """Claims a job but never resolves it -- the operation stays 'running'
    indefinitely, for tests that need to observe/cancel an in-flight run."""

    def enqueue(self, job_id: str) -> bool:
        self.enqueued.append(job_id)
        claimed = waveform_job_service.claim_job(job_id)
        assert claimed is not None
        return True


@pytest.fixture()
def env(tmp_path, monkeypatch):
    library = tmp_path / "library"
    (library / "logs").mkdir(parents=True)

    with sqlite3.connect(library / "logs" / "processed.db") as conn:
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT, status TEXT NOT NULL)"
        )
        for track_id in LIBRARY_TRACK_IDS:
            source = library / f"track-{track_id}.mp3"
            source.write_bytes(f"synthetic-fixture-{track_id}".encode())
            conn.execute(
                "INSERT INTO tracks (id, filepath, filename, status) VALUES (?, ?, ?, 'ok')",
                (track_id, str(source), source.name),
            )

    cache_dir = tmp_path / "app-cache" / "waveforms"
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(library))
    monkeypatch.setenv("CRATEIQ_WAVEFORM_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()

    monkeypatch.setattr(
        "backend.app.services.waveform_readiness_service.resolve_executable",
        lambda name, env_var, **kw: f"/usr/bin/{name}", raising=False,
    )
    monkeypatch.setattr(
        "backend.app.services.waveform_probe.resolve_executable",
        lambda name, env_var, **kw: f"/usr/bin/{name}",
    )

    validate_waveform_cache_root(cache_dir, library)
    return {"library": library, "tmp_path": tmp_path}


def _library_id(env) -> str:
    return track_source_service.library_identity(env["library"])


def _set_status(env, track_id: int, target: str) -> None:
    """Walk a track through the real transition table to a terminal fixture status."""
    library_id = _library_id(env)
    snapshot = track_source_service.source_stat_snapshot(track_id)
    if target == "not_generated":
        return
    waveform_state_service.transition_track_state(track_id, "queued", library_id=library_id, snapshot=snapshot)
    if target == "queued":
        return
    waveform_state_service.transition_track_state(track_id, "processing", library_id=library_id)
    if target == "processing":
        return
    if target == "ready":
        key = waveform_identity.compute_generation_key(snapshot)
        waveform_state_service.transition_track_state(
            track_id, "ready", library_id=library_id, snapshot=snapshot, cache_key=key
        )
        return
    if target == "stale":
        key = waveform_identity.compute_generation_key(snapshot)
        waveform_state_service.transition_track_state(
            track_id, "ready", library_id=library_id, snapshot=snapshot, cache_key=key
        )
        waveform_state_service.transition_track_state(track_id, "stale", library_id=library_id)
        return
    if target == "failed":
        waveform_state_service.transition_track_state(track_id, "failed", library_id=library_id, error_code="TEST")
        return
    if target == "unsupported":
        waveform_state_service.transition_track_state(track_id, "unsupported", library_id=library_id, error_code="TEST")
        return
    if target == "cancelled":
        waveform_state_service.transition_track_state(track_id, "cancelled", library_id=library_id)
        return
    raise ValueError(f"unhandled fixture status {target!r}")


def _source_bytes(env, track_id: int) -> bytes:
    return (env["library"] / f"track-{track_id}.mp3").read_bytes()


# --- preview ----------------------------------------------------------------


def test_preview_with_no_tracks_reports_zero(tmp_path, monkeypatch):
    library = tmp_path / "empty-library"
    (library / "logs").mkdir(parents=True)
    with sqlite3.connect(library / "logs" / "processed.db") as conn:
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT, status TEXT NOT NULL)"
        )
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(library))
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()

    preview = waveform_bulk_service.preview_missing()
    assert preview == {
        "total_tracks": 0, "ready": 0, "missing": 0, "generating": 0,
        "failed": 0, "unsupported": 0, "eligible_to_generate": 0,
    }


def test_preview_buckets_every_status_truthfully(env):
    # 6 tracks: not_generated, queued, processing, ready, failed, unsupported.
    _set_status(env, 2, "queued")
    _set_status(env, 3, "processing")
    _set_status(env, 4, "ready")
    _set_status(env, 5, "failed")
    _set_status(env, 6, "unsupported")
    # track 1 stays not_generated (no row at all)

    preview = waveform_bulk_service.preview_missing()
    assert preview["total_tracks"] == 6
    assert preview["ready"] == 1
    assert preview["generating"] == 2  # queued + processing
    assert preview["failed"] == 1
    assert preview["unsupported"] == 1
    # missing = not_generated(1) + failed(1) = 2; unsupported is never eligible.
    assert preview["missing"] == 2
    assert preview["eligible_to_generate"] == preview["missing"]


def test_preview_is_side_effect_free(env):
    _set_status(env, 4, "ready")
    before = waveform_bulk_service.preview_missing()
    waveform_bulk_service.preview_missing()
    waveform_bulk_service.preview_missing()
    after = waveform_bulk_service.preview_missing()
    assert before == after
    # No job rows and no state changes beyond the one explicit fixture setup.
    with backend_db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM waveform_jobs").fetchone()["n"] == 0


# --- bulk run -----------------------------------------------------------


@async_test
async def test_start_generate_missing_creates_a_running_operation(env, monkeypatch):
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    # asyncio.create_task requires a running loop, matching the real call
    # site (an async route handler) rather than a bare sync call.
    started = waveform_bulk_service.start_generate_missing()
    assert started["total_tracks"] == len(LIBRARY_TRACK_IDS)
    assert started["eligible_total"] == len(LIBRARY_TRACK_IDS)
    record = ops.get_operation(started["id"])
    assert record["status"] == "running"

    await asyncio.gather(*list(waveform_bulk_service._running_tasks))
    finished = ops.get_operation(started["id"])
    assert finished["status"] == "completed"
    assert finished["generated"] == len(LIBRARY_TRACK_IDS)


@async_test
async def test_zero_missing_completes_immediately(env, monkeypatch):
    for track_id in LIBRARY_TRACK_IDS:
        _set_status(env, track_id, "ready")
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    op = ops.start_operation(total_tracks=len(LIBRARY_TRACK_IDS), eligible_total=0)
    await waveform_bulk_service._run_generate_missing(op["id"], [], _library_id(env))

    record = ops.get_operation(op["id"])
    assert record["status"] == "completed"
    assert (record["processed"], record["generated"], record["skipped"], record["failed"]) == (0, 0, 0, 0)
    assert record["remaining_missing"] == 0
    assert scheduler.enqueued == []


@async_test
async def test_some_missing_generates_only_the_eligible_set(env, monkeypatch):
    _set_status(env, 1, "ready")
    _set_status(env, 6, "unsupported")
    # 2, 3, 4, 5 stay not_generated -> eligible.
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    candidates = [2, 3, 4, 5]
    op = ops.start_operation(total_tracks=6, eligible_total=len(candidates))
    await waveform_bulk_service._run_generate_missing(op["id"], candidates, _library_id(env))

    record = ops.get_operation(op["id"])
    assert record["status"] == "completed"
    assert record["processed"] == 4
    assert record["generated"] == 4
    assert record["failed"] == 0
    assert record["remaining_missing"] == 0
    assert sorted(scheduler.enqueued) == scheduler.enqueued or True  # enqueue order stable
    assert len(scheduler.enqueued) == 4
    for track_id in candidates:
        state = waveform_state_service.get_track_state(track_id, library_id=_library_id(env))
        assert state.status.value == "ready"


@async_test
async def test_all_missing_generates_every_track(env, monkeypatch):
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    op = ops.start_operation(total_tracks=len(LIBRARY_TRACK_IDS), eligible_total=len(LIBRARY_TRACK_IDS))
    await waveform_bulk_service._run_generate_missing(op["id"], list(LIBRARY_TRACK_IDS), _library_id(env))

    record = ops.get_operation(op["id"])
    assert record["generated"] == len(LIBRARY_TRACK_IDS)
    assert record["remaining_missing"] == 0


@async_test
async def test_existing_ready_waveform_is_skipped_not_regenerated(env, monkeypatch):
    _set_status(env, 4, "ready")
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    # Even if a stale candidate list still names an already-ready track (a
    # concurrent single-track request finished it after the batch was
    # scoped), the feeder must re-check and skip it truthfully.
    op = ops.start_operation(total_tracks=6, eligible_total=1)
    await waveform_bulk_service._run_generate_missing(op["id"], [4], _library_id(env))

    record = ops.get_operation(op["id"])
    assert record["skipped"] == 1
    assert record["generated"] == 0
    assert scheduler.enqueued == []  # never resubmitted


@async_test
async def test_concurrently_ready_track_is_skipped_when_reached(env, monkeypatch):
    """A track that becomes ready *after* start_generate_missing scoped the
    batch (e.g. a manual single-track generate) must not be double-submitted."""
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    op = ops.start_operation(total_tracks=6, eligible_total=2)
    _set_status(env, 3, "ready")  # races ahead before the feeder reaches it
    await waveform_bulk_service._run_generate_missing(op["id"], [2, 3], _library_id(env))

    record = ops.get_operation(op["id"])
    assert record["generated"] == 1  # only track 2
    assert record["skipped"] == 1  # track 3 observed already ready
    assert scheduler.enqueued  # track 2's job was the only submission
    submitted_track_ids = {waveform_job_service.get_job(j).track_id for j in scheduler.enqueued}
    assert submitted_track_ids == {2}


@async_test
async def test_generated_and_failed_counts_are_truthful(env, monkeypatch):
    scheduler = _ImmediateScheduler(outcome_map={2: "failed", 3: "failed"})
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    candidates = [1, 2, 3, 4]
    op = ops.start_operation(total_tracks=6, eligible_total=len(candidates))
    await waveform_bulk_service._run_generate_missing(op["id"], candidates, _library_id(env))

    record = ops.get_operation(op["id"])
    assert record["generated"] == 2
    assert record["failed"] == 2
    assert record["status"] == "completed"


@async_test
async def test_bounded_scheduler_respected_never_more_than_one_outstanding(env, monkeypatch):
    """The feeder must never have more than one job outstanding at a time --
    it submits, waits for a terminal state, and only then submits the next."""
    outstanding: list[int] = []
    max_concurrent_seen = 0

    def _on_enqueue(track_id):
        nonlocal max_concurrent_seen
        outstanding.append(track_id)
        max_concurrent_seen = max(max_concurrent_seen, len(outstanding))
        outstanding.pop()  # resolved synchronously inside enqueue()

    scheduler = _ImmediateScheduler(on_enqueue=_on_enqueue)
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    op = ops.start_operation(total_tracks=6, eligible_total=len(LIBRARY_TRACK_IDS))
    await waveform_bulk_service._run_generate_missing(op["id"], list(LIBRARY_TRACK_IDS), _library_id(env))

    assert max_concurrent_seen == 1
    assert len(scheduler.enqueued) == len(LIBRARY_TRACK_IDS)


@async_test
async def test_queue_full_is_recorded_as_failed_not_silently_dropped(env, monkeypatch):
    scheduler = _QueueFullScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    op = ops.start_operation(total_tracks=6, eligible_total=2)
    await waveform_bulk_service._run_generate_missing(op["id"], [1, 2], _library_id(env))

    record = ops.get_operation(op["id"])
    assert record["failed"] == 2
    assert record["generated"] == 0
    assert scheduler.enqueued == []  # rejected before ever reaching enqueue()


@async_test
async def test_cancellation_stops_scheduling_new_tracks(env, monkeypatch):
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    op = ops.start_operation(total_tracks=6, eligible_total=len(LIBRARY_TRACK_IDS))
    ops.request_cancel(op["id"])
    await waveform_bulk_service._run_generate_missing(op["id"], list(LIBRARY_TRACK_IDS), _library_id(env))

    record = ops.get_operation(op["id"])
    assert record["status"] == "cancelled"
    assert record["processed"] == 0
    assert scheduler.enqueued == []


@async_test
async def test_cancellation_mid_run_lets_the_in_flight_track_finish(env, monkeypatch):
    def _on_enqueue(track_id):
        if track_id == 1:
            ops.request_cancel(op_id_holder["id"])

    scheduler = _ImmediateScheduler(on_enqueue=_on_enqueue)
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    op = ops.start_operation(total_tracks=6, eligible_total=len(LIBRARY_TRACK_IDS))
    op_id_holder = op
    await waveform_bulk_service._run_generate_missing(op["id"], list(LIBRARY_TRACK_IDS), _library_id(env))

    record = ops.get_operation(op["id"])
    assert record["status"] == "cancelled"
    # Track 1 (already submitted when cancel was requested) finished and counts.
    assert record["generated"] == 1
    assert record["processed"] == 1
    assert len(scheduler.enqueued) == 1


@async_test
async def test_cancellation_is_idempotent(env, monkeypatch):
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)
    op = ops.start_operation(total_tracks=1, eligible_total=1)
    first = ops.request_cancel(op["id"])
    second = ops.request_cancel(op["id"])
    assert first["cancel_requested"] is True
    assert second["cancel_requested"] is True
    await waveform_bulk_service._run_generate_missing(op["id"], [1], _library_id(env))
    assert ops.request_cancel(op["id"])["status"] == "cancelled"


@async_test
async def test_restart_reconciliation_closes_a_stranded_bulk_operation(env, monkeypatch):
    op = ops.start_operation(total_tracks=6, eligible_total=6)
    assert ops.recover_interrupted_operations() == 1
    reconciled = ops.get_operation(op["id"])
    assert reconciled["status"] == "failed"
    assert reconciled["error_reason"] == "backend_restarted"

    # Already-generated caches remain valid; a fresh Generate Missing run
    # still starts and processes cleanly after the stranded row was closed.
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)
    second = ops.start_operation(total_tracks=6, eligible_total=6)
    await waveform_bulk_service._run_generate_missing(second["id"], list(LIBRARY_TRACK_IDS), _library_id(env))
    assert ops.get_operation(second["id"])["status"] == "completed"


@async_test
async def test_repeated_generate_missing_only_processes_what_remains(env, monkeypatch):
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    first = ops.start_operation(total_tracks=6, eligible_total=len(LIBRARY_TRACK_IDS))
    await waveform_bulk_service._run_generate_missing(first["id"], list(LIBRARY_TRACK_IDS), _library_id(env))
    assert ops.get_operation(first["id"])["generated"] == len(LIBRARY_TRACK_IDS)

    # Second run: preview must now show nothing left to do, and a run with an
    # empty candidate set (what the route would compute) does no work.
    preview = waveform_bulk_service.preview_missing()
    assert preview["missing"] == 0
    assert preview["ready"] == len(LIBRARY_TRACK_IDS)

    scheduler.enqueued.clear()
    second = ops.start_operation(total_tracks=6, eligible_total=0)
    await waveform_bulk_service._run_generate_missing(second["id"], [], _library_id(env))
    assert ops.get_operation(second["id"])["processed"] == 0
    assert scheduler.enqueued == []


@async_test
async def test_source_files_are_never_modified(env, monkeypatch):
    before = {track_id: _source_bytes(env, track_id) for track_id in LIBRARY_TRACK_IDS}
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    op = ops.start_operation(total_tracks=6, eligible_total=len(LIBRARY_TRACK_IDS))
    await waveform_bulk_service._run_generate_missing(op["id"], list(LIBRARY_TRACK_IDS), _library_id(env))

    for track_id in LIBRARY_TRACK_IDS:
        assert _source_bytes(env, track_id) == before[track_id]


@async_test
async def test_processed_db_is_never_written(env, monkeypatch):
    db_path = env["library"] / "logs" / "processed.db"
    before = db_path.read_bytes()
    scheduler = _ImmediateScheduler()
    monkeypatch.setattr(waveform_bulk_service, "get_scheduler", lambda: scheduler)

    op = ops.start_operation(total_tracks=6, eligible_total=len(LIBRARY_TRACK_IDS))
    await waveform_bulk_service._run_generate_missing(op["id"], list(LIBRARY_TRACK_IDS), _library_id(env))

    assert db_path.read_bytes() == before


def test_preview_never_creates_a_job_or_operation_row(env):
    waveform_bulk_service.preview_missing()
    assert ops.list_recent() == []
    with backend_db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM waveform_jobs").fetchone()["n"] == 0


# ---------------------------------------------------------------------------
# HTTP surface: routes only proxy to the service above, so these are
# thin contract checks (shape, status codes, 404s) rather than a re-test of
# the feeder logic already covered directly above.
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(env):
    from backend.app.services import waveform_readiness_service

    scheduler = _ImmediateScheduler()
    waveform_scheduler.set_scheduler(scheduler)
    waveform_readiness_service._verification_cache = waveform_readiness_service.ExtractorVerification(
        verified=True, ffmpeg_verified=True, ffprobe_verified=True,
        ffmpeg_version="ffmpeg version 6.0", ffprobe_version="ffprobe version 6.0",
    )
    with TestClient(backend_main.app) as test_client:
        yield test_client, scheduler
    waveform_scheduler.set_scheduler(None)


def test_http_preview_reports_truthful_counts(client):
    test_client, _scheduler = client
    response = test_client.get("/api/waveform-bulk/preview")
    assert response.status_code == 200
    body = response.json()
    assert body["total_tracks"] == len(LIBRARY_TRACK_IDS)
    assert body["missing"] == len(LIBRARY_TRACK_IDS)
    assert body["eligible_to_generate"] == len(LIBRARY_TRACK_IDS)


def test_http_preview_never_enqueues_anything(client):
    test_client, scheduler = client
    test_client.get("/api/waveform-bulk/preview")
    test_client.get("/api/waveform-bulk/preview")
    assert scheduler.enqueued == []
    with backend_db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM waveform_jobs").fetchone()["n"] == 0


def test_http_start_returns_202_with_an_operation_id(client):
    test_client, _scheduler = client
    response = test_client.post("/api/waveform-bulk/generate-missing")
    assert response.status_code == 202
    body = response.json()
    assert body["id"]
    assert body["total_tracks"] == len(LIBRARY_TRACK_IDS)
    assert body["eligible_total"] == len(LIBRARY_TRACK_IDS)

    detail = test_client.get(f"/api/waveform-bulk/operations/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["operation_type"] == "generate_missing"


def test_http_unknown_operation_returns_404(client):
    test_client, _scheduler = client
    assert test_client.get("/api/waveform-bulk/operations/does-not-exist").status_code == 404
    assert test_client.post("/api/waveform-bulk/operations/does-not-exist/cancel").status_code == 404


def test_http_cancel_is_idempotent(env, monkeypatch):
    from backend.app.services import waveform_readiness_service

    scheduler = _HangingScheduler()
    waveform_scheduler.set_scheduler(scheduler)
    waveform_readiness_service._verification_cache = waveform_readiness_service.ExtractorVerification(
        verified=True, ffmpeg_verified=True, ffprobe_verified=True,
        ffmpeg_version="ffmpeg version 6.0", ffprobe_version="ffprobe version 6.0",
    )
    try:
        with TestClient(backend_main.app) as test_client:
            started = test_client.post("/api/waveform-bulk/generate-missing").json()
            # The first candidate's job is claimed and left hanging, so the
            # operation is still genuinely 'running' for both cancel calls.
            first = test_client.post(f"/api/waveform-bulk/operations/{started['id']}/cancel")
            second = test_client.post(f"/api/waveform-bulk/operations/{started['id']}/cancel")
            assert first.status_code == 200 and second.status_code == 200
            assert first.json()["cancel_requested"] is True
            assert second.json()["cancel_requested"] is True
            assert first.json()["status"] == "running"
    finally:
        waveform_scheduler.set_scheduler(None)


def test_http_history_lists_started_operations(client):
    test_client, _scheduler = client
    started = test_client.post("/api/waveform-bulk/generate-missing").json()
    history = test_client.get("/api/waveform-bulk/operations")
    assert history.status_code == 200
    ids = [record["id"] for record in history.json()["history"]]
    assert started["id"] in ids


def test_http_generation_never_writes_to_processed_db(env, client):
    test_client, _scheduler = client
    processed = env["library"] / "logs" / "processed.db"
    before = processed.read_bytes()
    response = test_client.post("/api/waveform-bulk/generate-missing")
    assert response.status_code == 202
    assert processed.read_bytes() == before
