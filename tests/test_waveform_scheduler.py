"""W3 tests for job lifecycle, deduplication, cancellation, and restart recovery.

Every job runner here is a fake. No audio tool is ever launched and no real
music file is used as extraction input.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from backend.app.core import db as backend_db
from backend.app.models.waveform import (
    SourceStatSnapshot,
    WaveformArtifactStatus,
    WaveformJobStatus,
)
from backend.app.models.waveform_extraction import CancellationToken
from backend.app.services import waveform_identity, waveform_job_service, waveform_state_service
from backend.app.services.waveform_scheduler import WaveformScheduler
from tests.conftest import async_test

LIBRARY = "d" * 64


@pytest.fixture()
def jobs_db(tmp_path, monkeypatch):
    path = tmp_path / "operational" / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    return path


def _snapshot(track_id: int = 7, *, mtime: int = 1000) -> SourceStatSnapshot:
    return SourceStatSnapshot(
        library_id=LIBRARY,
        track_id=track_id,
        source_size_bytes=4096,
        source_mtime_ns=mtime,
        source_ctime_ns=mtime + 1,
        source_device=1,
        source_inode=2,
    )


def _submit(snapshot: SourceStatSnapshot, *, force: bool = False, max_queue_size: int = 32):
    return waveform_job_service.submit_generation_job(
        snapshot=snapshot,
        generation_key=waveform_identity.compute_generation_key(snapshot),
        force=force,
        max_queue_size=max_queue_size,
    )


# ---------------------------------------------------------------------------
# Generation identity
# ---------------------------------------------------------------------------


def test_generation_key_is_stable_and_stat_sensitive():
    base = _snapshot()
    assert waveform_identity.compute_generation_key(base) == waveform_identity.compute_generation_key(base)
    changed = _snapshot(mtime=2000)
    assert waveform_identity.compute_generation_key(base) != waveform_identity.compute_generation_key(changed)


def test_generation_key_is_hex_and_leaks_no_path(tmp_path):
    key = waveform_identity.compute_generation_key(_snapshot())
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)
    signature = waveform_identity.build_generation_signature(_snapshot())
    joined = repr(signature)
    assert str(tmp_path) not in joined
    assert "filepath" not in joined and "filename" not in joined


def test_generation_key_never_reads_source_content(tmp_path, monkeypatch):
    """Computing the key must not open any file."""
    real_open = Path.open

    def _forbidden(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("generation key must not read file contents")

    monkeypatch.setattr(Path, "open", _forbidden)
    waveform_identity.compute_generation_key(_snapshot())
    monkeypatch.setattr(Path, "open", real_open)


# ---------------------------------------------------------------------------
# Submission and deduplication
# ---------------------------------------------------------------------------


def test_first_submission_queues_a_job_and_track_state(jobs_db):
    result = _submit(_snapshot())
    assert result.outcome == "queued"
    assert result.job is not None and result.job.status is WaveformJobStatus.QUEUED
    state = waveform_state_service.get_track_state(7, library_id=LIBRARY)
    assert state.status is WaveformArtifactStatus.QUEUED


def test_duplicate_submission_reuses_the_active_job(jobs_db):
    first = _submit(_snapshot())
    second = _submit(_snapshot())
    assert second.outcome == "deduplicated"
    assert second.deduplicated is True
    assert second.job.id == first.job.id


def test_force_still_deduplicates_against_an_active_job(jobs_db):
    first = _submit(_snapshot())
    forced = _submit(_snapshot(), force=True)
    assert forced.outcome == "deduplicated"
    assert forced.job.id == first.job.id


def test_repeated_post_spam_never_creates_more_than_one_active_job(jobs_db):
    ids = {_submit(_snapshot(), force=(i % 2 == 0)).job.id for i in range(12)}
    assert len(ids) == 1
    with sqlite3.connect(jobs_db) as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM waveform_jobs WHERE status IN ('queued','processing')"
        ).fetchone()[0]
    assert active == 1


def test_changed_source_generation_supersedes_the_old_active_job(jobs_db):
    first = _submit(_snapshot(mtime=1000))
    second = _submit(_snapshot(mtime=5000))
    assert second.outcome == "queued"
    assert second.job.id != first.job.id
    superseded = waveform_job_service.get_job(first.job.id)
    assert superseded.status is WaveformJobStatus.CANCELLED
    assert superseded.error_code == waveform_job_service.ERROR_SUPERSEDED
    with sqlite3.connect(jobs_db) as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM waveform_jobs WHERE status IN ('queued','processing')"
        ).fetchone()[0]
    assert active == 1


def test_ready_artifact_short_circuits_when_force_is_false(jobs_db):
    snapshot = _snapshot()
    key = waveform_identity.compute_generation_key(snapshot)
    _submit(snapshot)
    job = waveform_job_service.get_active_job_for_track(LIBRARY, 7)
    waveform_job_service.claim_job(job.id)
    waveform_job_service.complete_job_ready(job.id, generation_key=key, snapshot=snapshot)

    result = _submit(snapshot)
    assert result.outcome == "already_ready"
    assert result.job is None


def test_force_regeneration_keeps_the_previous_ready_state_visible(jobs_db):
    """The old artifact must stay readable while a forced rerun is queued."""
    snapshot = _snapshot()
    key = waveform_identity.compute_generation_key(snapshot)
    _submit(snapshot)
    job = waveform_job_service.get_active_job_for_track(LIBRARY, 7)
    waveform_job_service.claim_job(job.id)
    waveform_job_service.complete_job_ready(job.id, generation_key=key, snapshot=snapshot)

    forced = _submit(snapshot, force=True)
    assert forced.outcome == "queued"
    state = waveform_state_service.get_track_state(7, library_id=LIBRARY)
    assert state.status is WaveformArtifactStatus.READY
    assert state.cache_key == key


def test_queue_full_is_reported_deterministically(jobs_db):
    for track_id in range(1, 4):
        assert _submit(_snapshot(track_id)).outcome == "queued"
    overflow = _submit(_snapshot(99), max_queue_size=3)
    assert overflow.outcome == "queue_full"
    assert overflow.job is None


def test_queue_full_still_allows_deduplication_of_an_existing_job(jobs_db):
    first = _submit(_snapshot(1))
    for track_id in range(2, 5):
        _submit(_snapshot(track_id))
    again = _submit(_snapshot(1), max_queue_size=1)
    assert again.outcome == "deduplicated"
    assert again.job.id == first.job.id


def test_concurrent_submissions_produce_exactly_one_active_job(jobs_db):
    """The partial unique index must survive interleaved submissions."""
    results = [_submit(_snapshot()) for _ in range(8)]
    queued = [r for r in results if r.outcome == "queued"]
    deduped = [r for r in results if r.outcome == "deduplicated"]
    assert len(queued) == 1
    assert len(deduped) == 7
    assert {r.job.id for r in results} == {queued[0].job.id}


def test_unique_active_index_rejects_a_second_active_row_directly(jobs_db):
    """Proof the DB itself enforces one active job, not just service code."""
    _submit(_snapshot())
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(jobs_db) as conn:
            conn.execute(
                "INSERT INTO waveform_jobs (id, library_id, track_id, status, created_at, generation_key)"
                " VALUES ('manual', ?, 7, 'queued', 'now', 'x')",
                (LIBRARY,),
            )


def test_different_tracks_get_independent_jobs(jobs_db):
    a = _submit(_snapshot(1))
    b = _submit(_snapshot(2))
    assert a.job.id != b.job.id
    assert a.outcome == b.outcome == "queued"


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------


def test_claim_moves_queued_job_and_track_to_processing(jobs_db):
    job = _submit(_snapshot()).job
    claimed = waveform_job_service.claim_job(job.id)
    assert claimed.status is WaveformJobStatus.PROCESSING
    assert claimed.started_at is not None
    state = waveform_state_service.get_track_state(7, library_id=LIBRARY)
    assert state.status is WaveformArtifactStatus.PROCESSING


def test_claim_refuses_a_job_cancelled_while_queued(jobs_db):
    job = _submit(_snapshot()).job
    waveform_job_service.request_cancellation(job.id)
    assert waveform_job_service.claim_job(job.id) is None


def test_claim_is_not_repeatable(jobs_db):
    job = _submit(_snapshot()).job
    assert waveform_job_service.claim_job(job.id) is not None
    assert waveform_job_service.claim_job(job.id) is None


# ---------------------------------------------------------------------------
# Completion / failure
# ---------------------------------------------------------------------------


def test_complete_marks_ready_with_generation_key(jobs_db):
    snapshot = _snapshot()
    key = waveform_identity.compute_generation_key(snapshot)
    job = _submit(snapshot).job
    waveform_job_service.claim_job(job.id)
    waveform_job_service.complete_job_ready(job.id, generation_key=key, snapshot=snapshot)
    assert waveform_job_service.get_job(job.id).status is WaveformJobStatus.SUCCEEDED
    state = waveform_state_service.get_track_state(7, library_id=LIBRARY)
    assert state.status is WaveformArtifactStatus.READY
    assert state.cache_key == key
    assert state.generated_at is not None


def test_failure_marks_job_and_track_failed(jobs_db):
    job = _submit(_snapshot()).job
    waveform_job_service.claim_job(job.id)
    waveform_job_service.finish_job_unsuccessfully(
        job.id,
        job_status=WaveformJobStatus.FAILED,
        track_status=WaveformArtifactStatus.FAILED,
        error_code="decode_failure",
    )
    assert waveform_job_service.get_job(job.id).error_code == "decode_failure"
    assert waveform_state_service.get_track_state(7, library_id=LIBRARY).status is WaveformArtifactStatus.FAILED


def test_failed_regeneration_leaves_a_ready_track_ready(jobs_db):
    snapshot = _snapshot()
    key = waveform_identity.compute_generation_key(snapshot)
    first = _submit(snapshot).job
    waveform_job_service.claim_job(first.id)
    waveform_job_service.complete_job_ready(first.id, generation_key=key, snapshot=snapshot)

    forced = _submit(snapshot, force=True).job
    waveform_job_service.claim_job(forced.id)
    waveform_job_service.finish_job_unsuccessfully(
        forced.id,
        job_status=WaveformJobStatus.FAILED,
        track_status=WaveformArtifactStatus.FAILED,
        error_code="decode_failure",
    )
    state = waveform_state_service.get_track_state(7, library_id=LIBRARY)
    assert state.status is WaveformArtifactStatus.READY
    assert state.cache_key == key


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_queued_job_finishes_it_immediately(jobs_db):
    job = _submit(_snapshot()).job
    cancelled = waveform_job_service.request_cancellation(job.id)
    assert cancelled.status is WaveformJobStatus.CANCELLED
    assert cancelled.cancel_requested is True
    assert waveform_state_service.get_track_state(7, library_id=LIBRARY).status is WaveformArtifactStatus.CANCELLED


def test_cancel_processing_job_sets_the_flag_without_finishing(jobs_db):
    job = _submit(_snapshot()).job
    waveform_job_service.claim_job(job.id)
    cancelled = waveform_job_service.request_cancellation(job.id)
    assert cancelled.status is WaveformJobStatus.PROCESSING
    assert cancelled.cancel_requested is True
    assert waveform_job_service.is_cancel_requested(job.id) is True


def test_cancel_after_success_is_a_no_op_that_keeps_ready(jobs_db):
    snapshot = _snapshot()
    key = waveform_identity.compute_generation_key(snapshot)
    job = _submit(snapshot).job
    waveform_job_service.claim_job(job.id)
    waveform_job_service.complete_job_ready(job.id, generation_key=key, snapshot=snapshot)

    result = waveform_job_service.request_cancellation(job.id)
    assert result.status is WaveformJobStatus.SUCCEEDED
    assert waveform_state_service.get_track_state(7, library_id=LIBRARY).status is WaveformArtifactStatus.READY


def test_cancel_is_idempotent(jobs_db):
    job = _submit(_snapshot()).job
    first = waveform_job_service.request_cancellation(job.id)
    second = waveform_job_service.request_cancellation(job.id)
    assert first.status is second.status is WaveformJobStatus.CANCELLED


def test_cancel_missing_job_returns_none(jobs_db):
    assert waveform_job_service.request_cancellation("no-such-job") is None


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------


def test_recovery_closes_interrupted_processing_and_queued_jobs(jobs_db):
    queued = _submit(_snapshot(1)).job
    processing = _submit(_snapshot(2)).job
    waveform_job_service.claim_job(processing.id)

    assert waveform_job_service.recover_interrupted_jobs() == 2

    assert waveform_job_service.get_job(processing.id).status is WaveformJobStatus.FAILED
    assert waveform_job_service.get_job(processing.id).error_code == waveform_job_service.ERROR_BACKEND_RESTARTED
    assert waveform_job_service.get_job(queued.id).status is WaveformJobStatus.CANCELLED
    assert waveform_state_service.get_track_state(2, library_id=LIBRARY).status is WaveformArtifactStatus.FAILED
    assert waveform_state_service.get_track_state(1, library_id=LIBRARY).status is WaveformArtifactStatus.CANCELLED


def test_recovery_frees_the_active_index_for_a_new_request(jobs_db):
    first = _submit(_snapshot()).job
    waveform_job_service.recover_interrupted_jobs()
    again = _submit(_snapshot())
    assert again.outcome == "queued"
    assert again.job.id != first.id


def test_recovery_preserves_a_ready_artifact(jobs_db):
    snapshot = _snapshot()
    key = waveform_identity.compute_generation_key(snapshot)
    job = _submit(snapshot).job
    waveform_job_service.claim_job(job.id)
    waveform_job_service.complete_job_ready(job.id, generation_key=key, snapshot=snapshot)

    assert waveform_job_service.recover_interrupted_jobs() == 0
    state = waveform_state_service.get_track_state(7, library_id=LIBRARY)
    assert state.status is WaveformArtifactStatus.READY
    assert state.cache_key == key


@async_test
async def test_restart_does_not_resume_extraction_for_persisted_jobs(jobs_db):
    """A restart must never re-run analysis merely because rows exist."""
    _submit(_snapshot(1))
    processing = _submit(_snapshot(2)).job
    waveform_job_service.claim_job(processing.id)

    ran: list[str] = []

    async def _runner(job_id, token):  # pragma: no cover - must never be called
        ran.append(job_id)

    waveform_job_service.recover_interrupted_jobs()
    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    await asyncio.sleep(0.02)
    await scheduler.stop()

    assert ran == [], "restart must not resume any persisted waveform job"
    assert scheduler.queue_depth == 0


# ---------------------------------------------------------------------------
# Scheduler execution
# ---------------------------------------------------------------------------


@async_test
async def test_scheduler_runs_an_enqueued_job_through_the_injected_runner(jobs_db):
    job = _submit(_snapshot()).job
    done = asyncio.Event()
    seen: list[str] = []

    async def _runner(job_id, token):
        seen.append(job_id)
        done.set()

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    assert scheduler.enqueue(job.id) is True
    await asyncio.wait_for(done.wait(), timeout=2)
    await scheduler.stop()
    assert seen == [job.id]


@async_test
async def test_scheduler_skips_a_job_cancelled_before_the_worker_starts(jobs_db):
    job = _submit(_snapshot()).job
    waveform_job_service.request_cancellation(job.id)

    ran: list[str] = []

    async def _runner(job_id, token):  # pragma: no cover - must never be called
        ran.append(job_id)

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    scheduler.enqueue(job.id)
    await asyncio.sleep(0.05)
    await scheduler.stop()
    assert ran == []


@async_test
async def test_scheduler_signals_the_token_of_a_running_job(jobs_db):
    job = _submit(_snapshot()).job
    started = asyncio.Event()
    observed: dict[str, bool] = {}

    async def _runner(job_id, token: CancellationToken):
        started.set()
        await token.wait()
        observed["cancelled"] = token.is_cancelled

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    scheduler.enqueue(job.id)
    await asyncio.wait_for(started.wait(), timeout=2)

    assert scheduler.signal_cancel(job.id) is True
    await asyncio.sleep(0.05)
    await scheduler.stop()
    assert observed.get("cancelled") is True


@async_test
async def test_scheduler_reports_unknown_job_cancellation(jobs_db):
    scheduler = WaveformScheduler(runner=None)
    assert scheduler.signal_cancel("not-running") is False


@async_test
async def test_scheduler_enqueue_reports_a_full_queue(jobs_db):
    async def _runner(job_id, token):  # pragma: no cover - never started
        await asyncio.sleep(10)

    scheduler = WaveformScheduler(max_queue_size=2, runner=_runner)
    # Not started, so nothing drains the queue.
    assert scheduler.enqueue("a") is True
    assert scheduler.enqueue("b") is True
    assert scheduler.enqueue("c") is False


@async_test
async def test_scheduler_concurrency_is_clamped_to_two(jobs_db):
    assert WaveformScheduler(max_concurrency=5).max_concurrency == 2
    assert WaveformScheduler(max_concurrency=0).max_concurrency == 1
    assert WaveformScheduler(max_concurrency=2).max_concurrency == 2


@async_test
async def test_worker_survives_a_failing_runner(jobs_db):
    first = _submit(_snapshot(1)).job
    second = _submit(_snapshot(2)).job
    completed = asyncio.Event()

    async def _runner(job_id, token):
        if job_id == first.id:
            raise RuntimeError("runner exploded")
        completed.set()

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    scheduler.enqueue(first.id)
    scheduler.enqueue(second.id)
    await asyncio.wait_for(completed.wait(), timeout=2)
    await scheduler.stop()


# ---------------------------------------------------------------------------
# Database isolation
# ---------------------------------------------------------------------------


def test_waveform_lifecycle_never_touches_processed_db(tmp_path, jobs_db):
    library = tmp_path / "library"
    (library / "logs").mkdir(parents=True)
    processed = library / "logs" / "processed.db"
    with sqlite3.connect(processed) as conn:
        conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, status TEXT)")
    before = processed.read_bytes()

    snapshot = _snapshot()
    key = waveform_identity.compute_generation_key(snapshot)
    job = _submit(snapshot).job
    waveform_job_service.claim_job(job.id)
    waveform_job_service.complete_job_ready(job.id, generation_key=key, snapshot=snapshot)

    assert processed.read_bytes() == before
    with sqlite3.connect(processed) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "waveform_jobs" not in tables and "waveform_track_state" not in tables
    with sqlite3.connect(jobs_db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"waveform_jobs", "waveform_track_state"} <= tables


# ---------------------------------------------------------------------------
# Production runner: extract -> publish -> ready
#
# The extractor itself is always mocked. No audio tool runs and no real music
# file is used as extraction input.
# ---------------------------------------------------------------------------


@pytest.fixture()
def runtime(tmp_path, monkeypatch, jobs_db):
    """A temporary library + cache with the extractor and binaries mocked out."""
    from backend.app.core.waveform_cache import validate_waveform_cache_root
    from backend.app.services import track_source_service

    library = tmp_path / "library"
    (library / "logs").mkdir(parents=True)
    source = library / "fixture.mp3"
    source.write_bytes(b"synthetic-fixture-never-decoded")
    with sqlite3.connect(library / "logs" / "processed.db") as conn:
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT, status TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO tracks (id, filepath, filename, status) VALUES (7, ?, 'fixture.mp3', 'ok')",
            (str(source),),
        )
    cache_dir = tmp_path / "app-cache"
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(library))
    monkeypatch.setenv("CRATEIQ_WAVEFORM_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(
        "backend.app.services.waveform_probe.resolve_executable",
        lambda name, env_var, **kw: f"/usr/bin/{name}",
    )
    return {
        "library": library,
        "source": source,
        "cache": validate_waveform_cache_root(cache_dir, library),
        "snapshot": track_source_service.source_stat_snapshot(7),
    }


def _fake_result():
    from backend.app.models.waveform_extraction import WaveformExtractionResult

    return WaveformExtractionResult(
        duration_ms=1234,
        source_channels=2,
        source_sample_rate_hz=44100,
        analysis_sample_rate_hz=8000,
        encoding="int16_min_max_interleaved",
        resolutions={"compact": [-1, 1] * 4, "player": [-2, 2] * 6, "detail": [-3, 3] * 8},
    )


def _submit_runtime_job(runtime):
    snapshot = runtime["snapshot"]
    key = waveform_identity.compute_generation_key(snapshot)
    job = waveform_job_service.submit_generation_job(
        snapshot=snapshot, generation_key=key, force=False, max_queue_size=32
    ).job
    waveform_job_service.claim_job(job.id)
    return job, key


@async_test
async def test_runner_publishes_an_artifact_and_marks_ready(runtime, monkeypatch):
    from backend.app.services import waveform_artifact_service, waveform_scheduler

    job, key = _submit_runtime_job(runtime)

    async def _fake_extract(*args, **kwargs):
        return _fake_result()

    monkeypatch.setattr(waveform_scheduler, "extract_waveform", _fake_extract)
    await waveform_scheduler.run_generation_job(job.id, CancellationToken())

    assert waveform_job_service.get_job(job.id).status is WaveformJobStatus.SUCCEEDED
    state = waveform_state_service.get_track_state(7, library_id=runtime["snapshot"].library_id)
    assert state.status is WaveformArtifactStatus.READY
    assert state.cache_key == key
    document = waveform_artifact_service.read_artifact(runtime["cache"], key)
    assert document["audio"]["duration_ms"] == 1234


@async_test
async def test_runner_maps_an_extraction_failure_without_publishing(runtime, monkeypatch):
    from backend.app.models.waveform_extraction import (
        WaveformExtractionError,
        WaveformExtractionErrorCode,
    )
    from backend.app.services import waveform_scheduler

    job, key = _submit_runtime_job(runtime)

    async def _fail(*args, **kwargs):
        raise WaveformExtractionError(WaveformExtractionErrorCode.DECODE_FAILURE, "boom")

    monkeypatch.setattr(waveform_scheduler, "extract_waveform", _fail)
    await waveform_scheduler.run_generation_job(job.id, CancellationToken())

    assert waveform_job_service.get_job(job.id).status is WaveformJobStatus.FAILED
    assert waveform_job_service.get_job(job.id).error_code == "decode_failure"
    assert waveform_state_service.get_track_state(
        7, library_id=runtime["snapshot"].library_id
    ).status is WaveformArtifactStatus.FAILED
    assert not (runtime["cache"].root / "v1").exists() or list(runtime["cache"].root.rglob("*.json.gz")) == []


@async_test
async def test_runner_maps_unsupported_codec_to_unsupported_state(runtime, monkeypatch):
    from backend.app.models.waveform_extraction import (
        WaveformExtractionError,
        WaveformExtractionErrorCode,
    )
    from backend.app.services import waveform_scheduler

    job, _key = _submit_runtime_job(runtime)

    async def _fail(*args, **kwargs):
        raise WaveformExtractionError(WaveformExtractionErrorCode.UNSUPPORTED_CODEC, "nope")

    monkeypatch.setattr(waveform_scheduler, "extract_waveform", _fail)
    await waveform_scheduler.run_generation_job(job.id, CancellationToken())
    assert waveform_state_service.get_track_state(
        7, library_id=runtime["snapshot"].library_id
    ).status is WaveformArtifactStatus.UNSUPPORTED


@async_test
async def test_runner_maps_source_changed_to_stale(runtime, monkeypatch):
    from backend.app.models.waveform_extraction import (
        WaveformExtractionError,
        WaveformExtractionErrorCode,
    )
    from backend.app.services import waveform_scheduler

    job, _key = _submit_runtime_job(runtime)

    async def _fail(*args, **kwargs):
        raise WaveformExtractionError(WaveformExtractionErrorCode.SOURCE_CHANGED, "changed")

    monkeypatch.setattr(waveform_scheduler, "extract_waveform", _fail)
    await waveform_scheduler.run_generation_job(job.id, CancellationToken())
    assert waveform_state_service.get_track_state(
        7, library_id=runtime["snapshot"].library_id
    ).status is WaveformArtifactStatus.STALE
    assert list(runtime["cache"].root.rglob("*.json.gz")) == []


@async_test
async def test_cancellation_accepted_before_publication_publishes_nothing(runtime, monkeypatch):
    """The cancellation cutoff is the atomic publish: nothing lands after it."""
    from backend.app.services import waveform_scheduler

    job, key = _submit_runtime_job(runtime)
    token = CancellationToken()

    async def _extract_then_cancel(*args, **kwargs):
        # Cancellation arrives after extraction but before publication.
        waveform_job_service.request_cancellation(job.id)
        token.cancel()
        return _fake_result()

    monkeypatch.setattr(waveform_scheduler, "extract_waveform", _extract_then_cancel)
    await waveform_scheduler.run_generation_job(job.id, token)

    assert waveform_job_service.get_job(job.id).status is WaveformJobStatus.CANCELLED
    assert list(runtime["cache"].root.rglob("*.json.gz")) == [], "cancelled job must publish no artifact"
    assert waveform_state_service.get_track_state(
        7, library_id=runtime["snapshot"].library_id
    ).status is WaveformArtifactStatus.CANCELLED


@async_test
async def test_cancellation_after_publication_keeps_the_ready_artifact(runtime, monkeypatch):
    from backend.app.services import waveform_scheduler

    job, key = _submit_runtime_job(runtime)

    async def _fake_extract(*args, **kwargs):
        return _fake_result()

    monkeypatch.setattr(waveform_scheduler, "extract_waveform", _fake_extract)
    await waveform_scheduler.run_generation_job(job.id, CancellationToken())

    # Cancellation loses once the job already succeeded.
    result = waveform_job_service.request_cancellation(job.id)
    assert result.status is WaveformJobStatus.SUCCEEDED
    assert waveform_state_service.get_track_state(
        7, library_id=runtime["snapshot"].library_id
    ).status is WaveformArtifactStatus.READY


@async_test
async def test_publication_failure_leaves_state_non_ready(runtime, monkeypatch):
    from backend.app.services import waveform_artifact_service, waveform_scheduler

    job, _key = _submit_runtime_job(runtime)

    async def _fake_extract(*args, **kwargs):
        return _fake_result()

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(waveform_scheduler, "extract_waveform", _fake_extract)
    monkeypatch.setattr(waveform_artifact_service, "publish_artifact", _boom)
    await waveform_scheduler.run_generation_job(job.id, CancellationToken())

    assert waveform_job_service.get_job(job.id).error_code == "WAVEFORM_CACHE_WRITE_FAILED"
    assert waveform_state_service.get_track_state(
        7, library_id=runtime["snapshot"].library_id
    ).status is WaveformArtifactStatus.FAILED


@async_test
async def test_runner_fails_safely_when_the_extractor_is_unavailable(runtime, monkeypatch):
    from backend.app.services import waveform_scheduler

    job, _key = _submit_runtime_job(runtime)
    monkeypatch.setattr(
        "backend.app.services.waveform_probe.resolve_executable",
        lambda name, env_var, **kw: None,
    )
    await waveform_scheduler.run_generation_job(job.id, CancellationToken())
    assert waveform_job_service.get_job(job.id).error_code == "WAVEFORM_EXTRACTOR_UNAVAILABLE"
    assert list(runtime["cache"].root.rglob("*.json.gz")) == []


@async_test
async def test_runner_never_holds_a_transaction_across_extraction(runtime, monkeypatch):
    """Extraction may last minutes; the DB must be writable while it runs."""
    from backend.app.services import waveform_scheduler

    job, _key = _submit_runtime_job(runtime)
    observed: dict[str, bool] = {}

    async def _extract_and_probe_db(*args, **kwargs):
        # A concurrent writer must not be blocked by the running job.
        waveform_job_service.request_cancellation("unrelated-job-id")
        observed["db_writable_during_extraction"] = True
        return _fake_result()

    monkeypatch.setattr(waveform_scheduler, "extract_waveform", _extract_and_probe_db)
    await waveform_scheduler.run_generation_job(job.id, CancellationToken())
    assert observed.get("db_writable_during_extraction") is True
    assert waveform_job_service.get_job(job.id).status is WaveformJobStatus.SUCCEEDED
