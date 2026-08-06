"""Bounded waveform generation scheduler and production job runner (W3).

Exactly one small asyncio queue plus a fixed worker pool serves the whole
process. There is no thread or task per API call, no unbounded queue, and no
automatic producer: the only thing that ever enqueues work is an explicit
generation request.

The runner is injectable so orchestration, cancellation, and persistence can
be tested end to end without invoking a real audio tool.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from ..core.waveform_config import load_waveform_config
from ..core.waveform_process import ProcessSupervisor
from ..models.waveform import WaveformArtifactStatus, WaveformJobStatus
from ..models.waveform_extraction import (
    CancellationToken,
    WaveformExtractionError,
    WaveformExtractionErrorCode,
)
from . import (
    track_source_service,
    waveform_artifact_service,
    waveform_cache_service,
    waveform_job_service,
)
from .waveform_extractor import extract_waveform
from .waveform_readiness_service import (
    WaveformRuntimeError,
    resolve_cache_runtime,
    resolve_extractor_binaries,
)

log = logging.getLogger(__name__)

JobRunner = Callable[[str, CancellationToken], Awaitable[None]]

# Artifact status applied when a job ends without publishing.
_FAILURE_TRACK_STATUS: dict[WaveformExtractionErrorCode, WaveformArtifactStatus] = {
    WaveformExtractionErrorCode.UNSUPPORTED_CODEC: WaveformArtifactStatus.UNSUPPORTED,
    WaveformExtractionErrorCode.SOURCE_CHANGED: WaveformArtifactStatus.STALE,
    WaveformExtractionErrorCode.CANCELLED: WaveformArtifactStatus.CANCELLED,
}


class WaveformScheduler:
    """A bounded queue plus a fixed number of cooperative workers."""

    def __init__(
        self,
        *,
        max_concurrency: int = 1,
        max_queue_size: int = 32,
        runner: JobRunner | None = None,
    ) -> None:
        self.max_concurrency = max(1, min(max_concurrency, 2))
        self.max_queue_size = max_queue_size
        self._runner: JobRunner = runner or run_generation_job
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max_queue_size)
        self._workers: list[asyncio.Task] = []
        self._tokens: dict[str, CancellationToken] = {}
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start idle workers. This never enqueues or resumes existing rows."""
        if self._started:
            return
        # Rebind the queue to the currently running loop so one scheduler
        # instance can be started again under a different loop.
        self._queue = asyncio.Queue(maxsize=self.max_queue_size)
        self._started = True
        self._workers = [
            asyncio.create_task(self._worker(index)) for index in range(self.max_concurrency)
        ]
        log.info("waveform scheduler started workers=%d queue_capacity=%d",
                 self.max_concurrency, self.max_queue_size)

    async def stop(self, *, drain_grace_seconds: float = 5.0) -> None:
        """Deterministic shutdown. Idempotent — a second call is a no-op.

        Order matters: stop accepting work, signal every in-flight job's
        cancellation token so W2's supervisor can TERM/KILL and reap its child
        process group, give workers a bounded moment to unwind, then cancel the
        tasks. Jobs still active afterwards are recorded as BACKEND_SHUTDOWN
        rather than as a user cancellation, and are never resumed on restart.
        """
        if not self._started:
            return
        self._started = False  # stop accepting new work immediately

        # Let running extractions tear their child processes down cleanly.
        for token in list(self._tokens.values()):
            token.cancel()

        if self._tokens and drain_grace_seconds > 0:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._workers, return_exceptions=True),
                    timeout=drain_grace_seconds,
                )
            except (asyncio.TimeoutError, Exception):  # noqa: B014 - shutdown must not raise
                pass

        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - shutdown must never raise
                log.exception("waveform worker raised during shutdown")
        self._workers.clear()
        self._tokens.clear()

        try:
            closed = waveform_job_service.fail_active_jobs_for_shutdown()
        except Exception:  # pragma: no cover - shutdown must never raise
            log.exception("waveform shutdown job reconciliation failed")
            closed = 0
        log.info("waveform scheduler stopped closed_active_jobs=%d", closed)

    @property
    def is_running(self) -> bool:
        return self._started

    # -- submission --------------------------------------------------------

    def enqueue(self, job_id: str) -> bool:
        """Enqueue an already-persisted job. Returns False when the queue is full."""
        try:
            self._queue.put_nowait(job_id)
            return True
        except asyncio.QueueFull:
            return False

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    # -- cancellation ------------------------------------------------------

    def signal_cancel(self, job_id: str) -> bool:
        """Signal a running job's cancellation token. False when not running."""
        token = self._tokens.get(job_id)
        if token is None:
            return False
        token.cancel()
        return True

    # -- worker ------------------------------------------------------------

    async def _worker(self, index: int) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._execute(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # a single bad job must never kill the worker
                log.exception("waveform worker %d failed job_id=%s", index, job_id)
            finally:
                self._queue.task_done()

    async def _execute(self, job_id: str) -> None:
        # A job cancelled while queued is never started.
        claimed = waveform_job_service.claim_job(job_id)
        if claimed is None:
            log.info("waveform job skipped job_id=%s reason=not_claimable", job_id)
            return
        token = CancellationToken()
        self._tokens[job_id] = token
        started = time.monotonic()
        try:
            await self._runner(job_id, token)
        except asyncio.CancelledError:
            # Shutdown or task cancellation: leave the row active so the
            # shutdown reconciliation records BACKEND_SHUTDOWN for it.
            raise
        except Exception:
            # An unexpected runner fault must not strand the job in
            # `processing`, which would permanently occupy this track's
            # active-job index and block every future request for it.
            log.exception("waveform job runner failed job_id=%s", job_id)
            try:
                waveform_job_service.finish_job_unsuccessfully(
                    job_id,
                    job_status=WaveformJobStatus.FAILED,
                    track_status=WaveformArtifactStatus.FAILED,
                    error_code=waveform_job_service.ERROR_WORKER_FAILED,
                )
            except Exception:  # pragma: no cover - never mask the original fault
                log.exception("waveform job failure bookkeeping failed job_id=%s", job_id)
        finally:
            self._tokens.pop(job_id, None)
            log.info(
                "waveform job finished job_id=%s track_id=%s elapsed_ms=%.0f",
                job_id, claimed.track_id, (time.monotonic() - started) * 1000,
            )


# ---------------------------------------------------------------------------
# Production runner
# ---------------------------------------------------------------------------


async def run_generation_job(job_id: str, token: CancellationToken) -> None:
    """Execute one claimed job: extract, publish atomically, then persist ready.

    Cancellation cutoff: the last cancellation check happens immediately
    before atomic publication. A cancellation accepted before that point
    publishes nothing; one arriving after finds a succeeded job and is a
    deterministic no-op. No DB transaction is held across extraction.
    """
    job = waveform_job_service.get_job(job_id)
    if job is None:  # pragma: no cover - claimed rows exist
        return
    generation_key = waveform_job_service.get_job_generation_key(job_id)
    if not generation_key:
        waveform_job_service.finish_job_unsuccessfully(
            job_id,
            job_status=WaveformJobStatus.FAILED,
            track_status=WaveformArtifactStatus.FAILED,
            error_code="WAVEFORM_MISSING_GENERATION_KEY",
        )
        return

    try:
        _config, validated_cache = resolve_cache_runtime()
        ffmpeg_bin, ffprobe_bin = resolve_extractor_binaries(validated_cache)
    except WaveformRuntimeError as exc:
        waveform_job_service.finish_job_unsuccessfully(
            job_id,
            job_status=WaveformJobStatus.FAILED,
            track_status=WaveformArtifactStatus.FAILED,
            error_code=exc.code,
        )
        return

    try:
        source = track_source_service.validated_track_source(job.track_id)
        snapshot = track_source_service.source_stat_snapshot(job.track_id)
    except (LookupError, ValueError, OSError):
        waveform_job_service.finish_job_unsuccessfully(
            job_id,
            job_status=WaveformJobStatus.FAILED,
            track_status=WaveformArtifactStatus.FAILED,
            error_code="WAVEFORM_SOURCE_UNAVAILABLE",
        )
        return

    try:
        result = await extract_waveform(
            source,
            snapshot,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            supervisor=ProcessSupervisor(),
            cancellation=token,
        )
    except WaveformExtractionError as exc:
        _finish_extraction_failure(job_id, exc)
        return

    # Final cancellation cutoff before anything becomes visible.
    if token.is_cancelled or waveform_job_service.is_cancel_requested(job_id):
        waveform_job_service.finish_job_unsuccessfully(
            job_id,
            job_status=WaveformJobStatus.CANCELLED,
            track_status=WaveformArtifactStatus.CANCELLED,
            error_code=None,
        )
        return

    try:
        document = waveform_artifact_service.build_artifact_document(
            result, generation_key=generation_key, snapshot=snapshot
        )
        waveform_artifact_service.validate_artifact_document(
            document, expected_generation_key=generation_key
        )
        payload = waveform_artifact_service.serialize_artifact(document)
        waveform_artifact_service.publish_artifact(validated_cache, generation_key, payload)
    except Exception:
        log.exception("waveform publication failed job_id=%s", job_id)
        waveform_job_service.finish_job_unsuccessfully(
            job_id,
            job_status=WaveformJobStatus.FAILED,
            track_status=WaveformArtifactStatus.FAILED,
            error_code="WAVEFORM_CACHE_WRITE_FAILED",
        )
        return

    waveform_job_service.complete_job_ready(
        job_id, generation_key=generation_key, snapshot=snapshot
    )
    log.info(
        "waveform ready job_id=%s track_id=%s duration_ms=%d detail_pairs=%d",
        job_id, job.track_id, result.duration_ms, len(result.resolutions.get("detail", [])) // 2,
    )

    # Bound cache growth right after a publication. The artifact just made
    # ready is protected so a publication can never immediately evict itself.
    # Cleanup failure must never turn a successful generation into a failure.
    try:
        await waveform_cache_service.cleanup_cache_locked(
            validated_cache,
            max_cache_bytes=_config.max_cache_bytes,
            protect_generation_key=generation_key,
        )
    except Exception:  # pragma: no cover - cleanup is best-effort
        log.exception("waveform cache cleanup after publication failed job_id=%s", job_id)


def _finish_extraction_failure(job_id: str, exc: WaveformExtractionError) -> None:
    cancelled = exc.code is WaveformExtractionErrorCode.CANCELLED
    waveform_job_service.finish_job_unsuccessfully(
        job_id,
        job_status=WaveformJobStatus.CANCELLED if cancelled else WaveformJobStatus.FAILED,
        track_status=_FAILURE_TRACK_STATUS.get(exc.code, WaveformArtifactStatus.FAILED),
        error_code=None if cancelled else exc.code.value,
    )
    log.info("waveform job unsuccessful job_id=%s code=%s", job_id, exc.code.value)


# ---------------------------------------------------------------------------
# Process-wide accessor
# ---------------------------------------------------------------------------

_scheduler: WaveformScheduler | None = None


def get_scheduler() -> WaveformScheduler:
    """Return the process scheduler, constructing an idle one on first use."""
    global _scheduler
    if _scheduler is None:
        try:
            config = load_waveform_config()
            _scheduler = WaveformScheduler(
                max_concurrency=config.max_concurrent_jobs,
                max_queue_size=config.max_queue_size,
            )
        except Exception:
            _scheduler = WaveformScheduler()
    return _scheduler


def set_scheduler(scheduler: WaveformScheduler | None) -> None:
    """Replace the process scheduler. Intended for lifespan wiring and tests."""
    global _scheduler
    _scheduler = scheduler
