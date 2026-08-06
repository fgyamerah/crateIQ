"""W6 tests for restart recovery, scheduler shutdown, worker resilience,
process-cancellation hardening, runtime readiness, resource limits, and
privacy-safe logging.

No real audio tool executes against media. The only subprocesses any test
allows are fake process objects; the runtime-readiness tests stub the version
check rather than invoking a binary on a media path.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sqlite3
from pathlib import Path

import pytest

from backend.app.core import db as backend_db
from backend.app.core import waveform_process
from backend.app.core.waveform_process import ProcessSupervisor
from backend.app.models.waveform import (
    SourceStatSnapshot,
    WaveformArtifactStatus,
    WaveformJobStatus,
)
from backend.app.models.waveform_extraction import CancellationToken
from backend.app.services import (
    waveform_identity,
    waveform_job_service,
    waveform_readiness_service,
    waveform_state_service,
)
from backend.app.services.waveform_scheduler import WaveformScheduler
from tests.conftest import async_test

LIBRARY = "e" * 64


@pytest.fixture()
def jobs_db(tmp_path, monkeypatch):
    path = tmp_path / "operational" / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    return path


def _snapshot(track_id: int = 7, *, mtime: int = 1000) -> SourceStatSnapshot:
    return SourceStatSnapshot(
        library_id=LIBRARY, track_id=track_id, source_size_bytes=4096,
        source_mtime_ns=mtime, source_ctime_ns=mtime + 1,
        source_device=1, source_inode=track_id,
    )


def _submit(track_id: int = 7, *, mtime: int = 1000):
    snapshot = _snapshot(track_id, mtime=mtime)
    return waveform_job_service.submit_generation_job(
        snapshot=snapshot,
        generation_key=waveform_identity.compute_generation_key(snapshot),
        force=False,
        max_queue_size=32,
    )


def _active_job_count(db_path) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM waveform_jobs WHERE status IN ('queued','processing')"
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------


def test_recovery_maps_each_status_correctly(jobs_db):
    queued = _submit(1).job
    processing = _submit(2).job
    waveform_job_service.claim_job(processing.id)

    assert waveform_job_service.recover_interrupted_jobs() == 2

    assert waveform_job_service.get_job(processing.id).status is WaveformJobStatus.FAILED
    assert waveform_job_service.get_job(queued.id).status is WaveformJobStatus.CANCELLED
    for job_id in (queued.id, processing.id):
        assert waveform_job_service.get_job(job_id).error_code == waveform_job_service.ERROR_BACKEND_RESTARTED


def test_recovery_leaves_terminal_jobs_untouched(jobs_db):
    snapshot = _snapshot(1)
    key = waveform_identity.compute_generation_key(snapshot)
    succeeded = _submit(1).job
    waveform_job_service.claim_job(succeeded.id)
    waveform_job_service.complete_job_ready(succeeded.id, generation_key=key, snapshot=snapshot)

    failed = _submit(2).job
    waveform_job_service.claim_job(failed.id)
    waveform_job_service.finish_job_unsuccessfully(
        failed.id, job_status=WaveformJobStatus.FAILED,
        track_status=WaveformArtifactStatus.FAILED, error_code="decode_failure",
    )
    cancelled = _submit(3).job
    waveform_job_service.request_cancellation(cancelled.id)

    before = {j: waveform_job_service.get_job(j) for j in (succeeded.id, failed.id, cancelled.id)}
    assert waveform_job_service.recover_interrupted_jobs() == 0
    after = {j: waveform_job_service.get_job(j) for j in (succeeded.id, failed.id, cancelled.id)}
    assert before == after, "terminal jobs must not be rewritten by recovery"


def test_repeated_recovery_is_idempotent(jobs_db):
    _submit(1)
    processing = _submit(2).job
    waveform_job_service.claim_job(processing.id)

    assert waveform_job_service.recover_interrupted_jobs() == 2
    snapshot_after_first = {
        row_id: waveform_job_service.get_job(row_id)
        for row_id in [processing.id]
    }
    # Repeated startups must not mutate rows again or invent new failures.
    assert waveform_job_service.recover_interrupted_jobs() == 0
    assert waveform_job_service.recover_interrupted_jobs() == 0
    assert waveform_job_service.get_job(processing.id) == snapshot_after_first[processing.id]


def test_recovery_creates_no_duplicate_rows(jobs_db):
    _submit(1)
    waveform_job_service.recover_interrupted_jobs()
    waveform_job_service.recover_interrupted_jobs()
    with sqlite3.connect(jobs_db) as conn:
        total = conn.execute("SELECT COUNT(*) FROM waveform_jobs").fetchone()[0]
    assert total == 1


def test_recovery_releases_the_active_job_slot(jobs_db):
    first = _submit(1).job
    waveform_job_service.claim_job(first.id)
    waveform_job_service.recover_interrupted_jobs()
    assert _active_job_count(jobs_db) == 0
    again = _submit(1)
    assert again.outcome == "queued", "a recovered track must be requestable again"


@async_test
async def test_recovery_never_re_enqueues_work(jobs_db):
    _submit(1)
    processing = _submit(2).job
    waveform_job_service.claim_job(processing.id)
    waveform_job_service.recover_interrupted_jobs()

    ran: list[str] = []

    async def _runner(job_id, token):  # pragma: no cover - must never be called
        ran.append(job_id)

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()
    assert ran == [], "restart must never resume analysis on its own"
    assert scheduler.queue_depth == 0


# ---------------------------------------------------------------------------
# Scheduler shutdown
# ---------------------------------------------------------------------------


@async_test
async def test_shutdown_with_no_work_is_clean(jobs_db):
    scheduler = WaveformScheduler(runner=None)
    await scheduler.start()
    await scheduler.stop()
    assert scheduler.is_running is False


@async_test
async def test_start_and_stop_are_idempotent(jobs_db):
    calls: list[str] = []

    async def _runner(job_id, token):
        calls.append(job_id)

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    await scheduler.start()  # duplicate start must not add a second worker pool
    assert len(scheduler._workers) == scheduler.max_concurrency
    await scheduler.stop()
    await scheduler.stop()  # duplicate stop must be a safe no-op
    assert scheduler.is_running is False
    assert scheduler._workers == []


@async_test
async def test_shutdown_cancels_an_active_extraction(jobs_db):
    job = _submit(1).job
    started = asyncio.Event()
    observed: dict[str, bool] = {}

    async def _runner(job_id, token: CancellationToken):
        started.set()
        await token.wait()
        observed["token_cancelled"] = token.is_cancelled
        raise asyncio.CancelledError()

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    scheduler.enqueue(job.id)
    await asyncio.wait_for(started.wait(), timeout=2)

    await scheduler.stop(drain_grace_seconds=1.0)

    assert observed.get("token_cancelled") is True, "shutdown must signal in-flight cancellation tokens"


@async_test
async def test_shutdown_records_backend_shutdown_not_user_cancellation(jobs_db):
    job = _submit(1).job
    started = asyncio.Event()

    async def _runner(job_id, token):
        started.set()
        await asyncio.sleep(30)

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    scheduler.enqueue(job.id)
    await asyncio.wait_for(started.wait(), timeout=2)

    await scheduler.stop(drain_grace_seconds=0.1)

    finished = waveform_job_service.get_job(job.id)
    assert finished.status is WaveformJobStatus.FAILED
    assert finished.error_code == waveform_job_service.ERROR_BACKEND_SHUTDOWN
    assert _active_job_count(jobs_db) == 0, "shutdown must release the active-job slot"


@async_test
async def test_shutdown_with_queued_but_unstarted_work(jobs_db):
    job = _submit(1).job
    scheduler = WaveformScheduler(runner=None)
    # Never started: the job stays queued in the DB and nothing runs.
    scheduler.enqueue(job.id)
    await scheduler.stop()
    assert waveform_job_service.get_job(job.id).status is WaveformJobStatus.QUEUED


@async_test
async def test_stop_before_start_is_safe(jobs_db):
    scheduler = WaveformScheduler(runner=None)
    await scheduler.stop()
    assert scheduler.is_running is False


# ---------------------------------------------------------------------------
# Worker resilience
# ---------------------------------------------------------------------------


@async_test
async def test_worker_survives_a_runner_exception_and_keeps_processing(jobs_db):
    first = _submit(1).job
    second = _submit(2).job
    done = asyncio.Event()

    async def _runner(job_id, token):
        if job_id == first.id:
            raise RuntimeError("runner exploded")
        done.set()

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    scheduler.enqueue(first.id)
    scheduler.enqueue(second.id)
    await asyncio.wait_for(done.wait(), timeout=3)
    await scheduler.stop()


@async_test
async def test_runner_exception_does_not_strand_the_job_in_processing(jobs_db):
    """A crashed runner must release the track's active-job slot."""
    job = _submit(1).job

    async def _runner(job_id, token):
        raise RuntimeError("boom")

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    scheduler.enqueue(job.id)
    await asyncio.sleep(0.3)
    await scheduler.stop()

    finished = waveform_job_service.get_job(job.id)
    assert finished.status is WaveformJobStatus.FAILED
    assert finished.error_code == waveform_job_service.ERROR_WORKER_FAILED
    assert _active_job_count(jobs_db) == 0


@async_test
async def test_track_is_requestable_again_after_a_worker_failure(jobs_db):
    job = _submit(1).job

    async def _runner(job_id, token):
        raise RuntimeError("boom")

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    scheduler.enqueue(job.id)
    await asyncio.sleep(0.3)
    await scheduler.stop()

    again = _submit(1)
    assert again.outcome == "queued", "a failed job must not block the track forever"


# ---------------------------------------------------------------------------
# Queue accounting
# ---------------------------------------------------------------------------


def test_queue_slot_released_after_cancellation(jobs_db):
    job = _submit(1).job
    assert waveform_job_service.count_queued_jobs() == 1
    waveform_job_service.request_cancellation(job.id)
    assert waveform_job_service.count_queued_jobs() == 0


def test_queue_slot_released_after_completion(jobs_db):
    snapshot = _snapshot(1)
    key = waveform_identity.compute_generation_key(snapshot)
    job = _submit(1).job
    waveform_job_service.claim_job(job.id)
    waveform_job_service.complete_job_ready(job.id, generation_key=key, snapshot=snapshot)
    assert waveform_job_service.count_queued_jobs() == 0


def test_no_permanent_queue_full_after_jobs_finish(jobs_db):
    jobs = [_submit(i).job for i in range(1, 4)]
    for job in jobs:
        waveform_job_service.request_cancellation(job.id)
    assert waveform_job_service.count_queued_jobs() == 0
    fresh = waveform_job_service.submit_generation_job(
        snapshot=_snapshot(99),
        generation_key=waveform_identity.compute_generation_key(_snapshot(99)),
        force=False,
        max_queue_size=3,
    )
    assert fresh.outcome == "queued", "finished jobs must free their queue slots"


def test_queue_capacity_still_enforced(jobs_db):
    for i in range(1, 4):
        assert _submit(i).outcome == "queued"
    overflow = waveform_job_service.submit_generation_job(
        snapshot=_snapshot(99),
        generation_key=waveform_identity.compute_generation_key(_snapshot(99)),
        force=False,
        max_queue_size=3,
    )
    assert overflow.outcome == "queue_full"


# ---------------------------------------------------------------------------
# Process-group cancellation hardening
# ---------------------------------------------------------------------------


class _HangingStream:
    async def read(self, n):  # noqa: ARG002
        await asyncio.Event().wait()
        return b""  # pragma: no cover


class _EmptyStream:
    async def read(self, n):  # noqa: ARG002
        return b""


class FakeProcess:
    def __init__(self, *, exit_code=0, respond_to_term=True, already_exited=False):
        self.pid = 4242
        self.returncode = exit_code if already_exited else None
        self._exit_code = exit_code
        self.stdout = _HangingStream()
        self.stderr = _EmptyStream()
        self.signals: list[int] = []
        self._event = asyncio.Event()
        self._respond_to_term = respond_to_term
        if already_exited:
            self._event.set()

    def send_signal(self, sig):
        self.signals.append(sig)
        if sig == signal.SIGKILL or (sig == signal.SIGTERM and self._respond_to_term):
            self._event.set()

    async def wait(self):
        await self._event.wait()
        self.returncode = self._exit_code
        return self._exit_code


def _spawn(proc):
    async def _inner(*argv, **kwargs):
        return proc
    return _inner


@pytest.fixture()
def force_signal_fallback(monkeypatch):
    """Fake pids are not real process groups; exercise the fallback path."""
    def _raise(_pid):
        raise ProcessLookupError()
    monkeypatch.setattr(waveform_process.os, "getpgid", _raise)


@async_test
async def test_term_is_sent_before_kill(jobs_db, force_signal_fallback):
    proc = FakeProcess(exit_code=-15, respond_to_term=True)
    supervisor = ProcessSupervisor(spawn=_spawn(proc))
    managed = await supervisor.run(["fake"], timeout_seconds=0.02, termination_grace_seconds=1.0)
    async for _ in managed.stdout:
        pass
    assert signal.SIGTERM in proc.signals
    assert signal.SIGKILL not in proc.signals


@async_test
async def test_kill_follows_when_term_is_ignored(jobs_db, force_signal_fallback):
    proc = FakeProcess(exit_code=-9, respond_to_term=False)
    supervisor = ProcessSupervisor(spawn=_spawn(proc))
    managed = await supervisor.run(["fake"], timeout_seconds=0.02, termination_grace_seconds=0.02)
    async for _ in managed.stdout:
        pass
    assert proc.signals[0] == signal.SIGTERM
    assert signal.SIGKILL in proc.signals
    assert managed.outcome.exit_code == -9, "the child must still be reaped"


@async_test
async def test_process_group_signal_is_preferred_when_available(jobs_db, monkeypatch):
    proc = FakeProcess(exit_code=-15, respond_to_term=True)
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(waveform_process.os, "getpgid", lambda pid: 777)

    def _killpg(pgid, sig):
        killed.append((pgid, sig))
        proc.send_signal(sig)

    monkeypatch.setattr(waveform_process.os, "killpg", _killpg)
    supervisor = ProcessSupervisor(spawn=_spawn(proc))
    managed = await supervisor.run(["fake"], timeout_seconds=0.02, termination_grace_seconds=1.0)
    async for _ in managed.stdout:
        pass
    assert killed and killed[0][0] == 777, "cancellation should target the whole process group"


@async_test
async def test_already_exited_child_terminates_cleanly(jobs_db, force_signal_fallback):
    proc = FakeProcess(exit_code=0, already_exited=True)
    proc.stdout = _EmptyStream()
    supervisor = ProcessSupervisor(spawn=_spawn(proc))
    managed = await supervisor.run(["fake"], timeout_seconds=5)
    async for _ in managed.stdout:
        pass
    assert managed.outcome.exit_code == 0


@async_test
async def test_repeated_cancellation_is_safe(jobs_db, force_signal_fallback):
    proc = FakeProcess(exit_code=-9, respond_to_term=False)
    supervisor = ProcessSupervisor(spawn=_spawn(proc))
    token = CancellationToken()
    token.cancel()
    token.cancel()  # cancelling twice must not raise
    managed = await supervisor.run(
        ["fake"], timeout_seconds=5, cancellation=token, termination_grace_seconds=0.02
    )
    async for _ in managed.stdout:
        pass
    assert managed.outcome.cancelled is True


def test_no_pid_is_persisted_in_jobs_db(jobs_db):
    """PID-reuse safety: a restart must have no PID it could blindly kill."""
    _submit(1)
    with sqlite3.connect(jobs_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(waveform_jobs)")}
    assert not {c for c in columns if "pid" in c.lower()}, (
        "waveform_jobs must never store a PID; after a restart it could name an unrelated process"
    )


def test_waveform_modules_never_persist_a_pid():
    """Cancellation must act on the live process object, not a stored PID."""
    services = Path("backend/app/services")
    for name in ("waveform_job_service.py", "waveform_scheduler.py", "waveform_state_service.py"):
        source = (services / name).read_text()
        assert "getpgid" not in source, f"{name} must not resolve process groups itself"
        assert ".pid" not in source, f"{name} must not read or store a process id"


# ---------------------------------------------------------------------------
# Runtime extractor readiness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_verification_cache():
    waveform_readiness_service.reset_extractor_verification()
    yield
    waveform_readiness_service.reset_extractor_verification()


def _tool_checks() -> list[dict]:
    return [
        {"name": "binary_ffmpeg", "status": "pass"},
        {"name": "binary_ffprobe", "status": "pass"},
    ]


def _readiness(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    monkeypatch.setattr(waveform_readiness_service.shutil, "which", lambda name: f"/usr/bin/{name}")
    return waveform_readiness_service.get_waveform_readiness(
        _tool_checks(),
        environ={"CRATEIQ_WAVEFORM_CACHE_DIR": str(tmp_path / "cache")},
        library_root=library,
    )


def test_readiness_reports_detected_before_verification(tmp_path, monkeypatch):
    result = _readiness(tmp_path, monkeypatch)
    assert result["status"] == "detected"
    assert result["engine"]["version_verified"] is False


def test_readiness_reports_ready_after_successful_verification(tmp_path, monkeypatch):
    waveform_readiness_service._verification_cache = waveform_readiness_service.ExtractorVerification(
        verified=True, ffmpeg_verified=True, ffprobe_verified=True,
        ffmpeg_version="ffmpeg version 6.0", ffprobe_version="ffprobe version 6.0",
    )
    result = _readiness(tmp_path, monkeypatch)
    assert result["status"] == "ready"
    assert result["engine"]["version_verified"] is True


def test_readiness_reports_unavailable_when_verification_failed(tmp_path, monkeypatch):
    waveform_readiness_service._verification_cache = waveform_readiness_service.ExtractorVerification(
        verified=False, ffmpeg_verified=True, ffprobe_verified=False,
        ffmpeg_version="ffmpeg version 6.0", ffprobe_version=None,
    )
    result = _readiness(tmp_path, monkeypatch)
    assert result["status"] == "extractor_unavailable"
    assert result["engine"]["version_verified"] is False


def test_readiness_never_leaks_paths_or_executables(tmp_path, monkeypatch):
    import json
    waveform_readiness_service._verification_cache = waveform_readiness_service.ExtractorVerification(
        verified=True, ffmpeg_verified=True, ffprobe_verified=True,
        ffmpeg_version="ffmpeg version 6.0", ffprobe_version="ffprobe version 6.0",
    )
    rendered = json.dumps(_readiness(tmp_path, monkeypatch))
    assert "/usr/bin" not in rendered
    assert str(tmp_path) not in rendered


@pytest.mark.parametrize(
    ("tool", "raw"),
    [
        ("ffmpeg", None),
        ("ffmpeg", ""),
        ("ffmpeg", "   "),
        ("ffmpeg", "not a version line"),
        ("ffmpeg", "ffprobe version 6.0"),      # wrong tool identity
        ("ffprobe", "ffmpeg version 6.0"),      # wrong tool identity
        ("ffmpeg", "ffmpeg"),                   # truncated
        ("ffmpeg", "x" * 500),                  # unbounded junk
    ],
)
def test_malformed_version_output_is_rejected(tool, raw):
    assert waveform_readiness_service._normalize_version(tool, raw) is None


@pytest.mark.parametrize(
    ("tool", "raw"),
    [
        ("ffmpeg", "ffmpeg version 6.0 Copyright (c) 2000-2023"),
        ("ffprobe", "ffprobe version n7.1.1-static"),
        ("ffmpeg", "FFmpeg version 4.4.2-0ubuntu0.22.04.1"),
    ],
)
def test_plausible_version_output_is_accepted(tool, raw):
    assert waveform_readiness_service._normalize_version(tool, raw) == raw.strip()


@async_test
async def test_verification_is_skipped_when_extractor_is_unavailable(tmp_path, monkeypatch):
    """Nothing is executed when the binaries were not even detected."""
    monkeypatch.setattr(
        "backend.app.services.waveform_probe.resolve_executable",
        lambda name, env_var, **kw: None,
    )
    monkeypatch.setenv("CRATEIQ_WAVEFORM_CACHE_DIR", str(tmp_path / "cache"))
    library = tmp_path / "library"
    library.mkdir()
    result = await waveform_readiness_service.verify_extractor_runtime(library_root=library)
    assert result is None
    assert waveform_readiness_service.cached_extractor_verification() is None


@async_test
async def test_verification_caches_its_result(tmp_path, monkeypatch):
    calls: list[str] = []

    async def _fake_versions(*, ffmpeg_bin, ffprobe_bin, supervisor, **kwargs):
        calls.append("ran")
        return {
            "ffmpeg_verified": True, "ffprobe_verified": True,
            "ffmpeg_version": "ffmpeg version 6.0", "ffprobe_version": "ffprobe version 6.0",
        }

    monkeypatch.setattr(
        "backend.app.services.waveform_probe.resolve_executable",
        lambda name, env_var, **kw: f"/usr/bin/{name}",
    )
    monkeypatch.setattr("backend.app.services.waveform_probe.verify_extractor_versions", _fake_versions)
    monkeypatch.setenv("CRATEIQ_WAVEFORM_CACHE_DIR", str(tmp_path / "cache"))
    library = tmp_path / "library"
    library.mkdir()

    first = await waveform_readiness_service.verify_extractor_runtime(library_root=library)
    second = await waveform_readiness_service.verify_extractor_runtime(library_root=library)
    assert first is not None and first.verified is True
    assert second == first
    assert len(calls) == 1, "verification must be cached, not repeated per call"


@async_test
async def test_verification_marks_unverified_when_one_tool_fails(tmp_path, monkeypatch):
    async def _fake_versions(*, ffmpeg_bin, ffprobe_bin, supervisor, **kwargs):
        return {
            "ffmpeg_verified": True, "ffprobe_verified": False,
            "ffmpeg_version": "ffmpeg version 6.0", "ffprobe_version": None,
        }

    monkeypatch.setattr(
        "backend.app.services.waveform_probe.resolve_executable",
        lambda name, env_var, **kw: f"/usr/bin/{name}",
    )
    monkeypatch.setattr("backend.app.services.waveform_probe.verify_extractor_versions", _fake_versions)
    monkeypatch.setenv("CRATEIQ_WAVEFORM_CACHE_DIR", str(tmp_path / "cache"))
    library = tmp_path / "library"
    library.mkdir()

    result = await waveform_readiness_service.verify_extractor_runtime(library_root=library)
    assert result is not None
    assert result.verified is False
    assert result.ffprobe_verified is False


def test_readiness_get_never_spawns_a_subprocess(tmp_path, monkeypatch):
    """The readiness report is a pure read of cached state."""
    import subprocess

    def _forbidden(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("readiness evaluation must not spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden)
    result = _readiness(tmp_path, monkeypatch)
    assert result["status"] in {"detected", "ready", "extractor_unavailable"}


# ---------------------------------------------------------------------------
# Resource limits — runtime enforcement
# ---------------------------------------------------------------------------


def test_concurrency_is_clamped_to_the_documented_maximum():
    assert WaveformScheduler(max_concurrency=99).max_concurrency == 2
    assert WaveformScheduler(max_concurrency=2).max_concurrency == 2
    assert WaveformScheduler(max_concurrency=1).max_concurrency == 1
    assert WaveformScheduler(max_concurrency=0).max_concurrency == 1


def test_documented_limits_have_the_expected_values():
    from backend.app.core import waveform_limits as limits

    assert limits.MAX_SOURCE_SIZE_BYTES == 8 * 1024 * 1024 * 1024
    assert limits.MAX_DURATION_SECONDS == 6 * 60 * 60
    assert limits.DECODER_THREADS == 1
    assert limits.DETAIL_PAIR_MAX == 32768
    assert limits.TERMINATION_GRACE_SECONDS == 5.0
    assert limits.UNKNOWN_DURATION_TIMEOUT_SECONDS == 600.0
    assert limits.MAX_TIMEOUT_SECONDS == 1200.0


def test_timeout_never_exceeds_the_documented_ceiling():
    from backend.app.core.waveform_limits import (
        MAX_TIMEOUT_SECONDS,
        MIN_TIMEOUT_SECONDS,
        compute_extraction_timeout_seconds,
    )

    for duration in (0, 1, 300, 3600, 6 * 3600, 99999):
        value = compute_extraction_timeout_seconds(duration)
        assert MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS


def test_decoder_threads_and_no_output_path_in_decode_argv():
    from backend.app.services.waveform_extractor import build_decode_argv

    argv = build_decode_argv("ffmpeg", "/library/track.mp3")
    assert argv[argv.index("-threads") + 1] == "1"
    assert argv[-1] == "pipe:1", "decode must stream to stdout, never to an output file"


def test_default_cache_limit_matches_the_architecture():
    from backend.app.core.waveform_config import DEFAULT_WAVEFORM_MAX_CACHE_BYTES, load_waveform_config

    assert DEFAULT_WAVEFORM_MAX_CACHE_BYTES == 2 * 1024 * 1024 * 1024
    config = load_waveform_config({}, backend_data_dir=Path("/tmp/never-created"))
    assert config.max_cache_bytes == DEFAULT_WAVEFORM_MAX_CACHE_BYTES
    assert config.max_concurrent_jobs == 1
    assert config.max_queue_size == 32


# ---------------------------------------------------------------------------
# Observability privacy
# ---------------------------------------------------------------------------


@async_test
async def test_lifecycle_logs_contain_no_private_paths(jobs_db, caplog, tmp_path):
    caplog.set_level(logging.INFO)
    job = _submit(1).job

    async def _runner(job_id, token):
        return None

    scheduler = WaveformScheduler(runner=_runner)
    await scheduler.start()
    scheduler.enqueue(job.id)
    await asyncio.sleep(0.3)
    await scheduler.stop()
    waveform_job_service.recover_interrupted_jobs()

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "/home/" not in rendered
    assert str(tmp_path) not in rendered
    assert ".mp3" not in rendered
    # Safe operational identifiers must still be present.
    assert "waveform scheduler started" in rendered
    assert job.id in rendered


def test_cleanup_logs_contain_no_paths(caplog, tmp_path, jobs_db):
    from backend.app.core.waveform_cache import validate_waveform_cache_root
    from backend.app.services import waveform_cache_service as cache_service

    caplog.set_level(logging.INFO)
    library = tmp_path / "music"
    library.mkdir()
    validated = validate_waveform_cache_root(tmp_path / "cache", library)
    key = f"{1:064x}"
    path = validated.root / "v1" / "mono-minmax-s16-v1" / key[:2] / f"{key}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 5000)

    cache_service.cleanup_cache(validated, max_cache_bytes=100)

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert str(tmp_path) not in rendered
    assert "/home/" not in rendered
    assert "freed_bytes" in rendered, "cleanup should still report safe operational counters"
