"""Waveform generation and cache lifecycle routes (W3, W6).

  GET    /api/tracks/{track_id}/waveform          — read-only state/peaks
  POST   /api/tracks/{track_id}/waveform/generate — explicit generation
  GET    /api/waveform-jobs/{job_id}              — job status
  DELETE /api/waveform-jobs/{job_id}              — best-effort cancellation
  GET    /api/waveform-cache                      — footprint + clear preview
  POST   /api/waveform-cache/clear                — confirmed cache clear (W6)

The GET endpoints are strictly side-effect free: they never enqueue a job, run
FFmpeg or ffprobe, read source audio content, or write a cache artifact. The
track GET performs only a database lookup, a cheap ``stat`` for staleness, and
a bounded read of an already-published artifact.

The only destructive route is the cache clear, and it requires explicit
confirmation. It can delete nothing but CrateIQ's own derived cache files
inside the validated waveform cache root.

No response here contains a source path, cache path, library root, executable
path, raw subprocess output, or a content hash.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ...models.waveform import (
    WAVEFORM_ALGORITHM_VERSION,
    WAVEFORM_SCHEMA_VERSION,
    WaveformArtifactStatus,
)
from ...schemas.waveform import (
    WaveformCacheClearRequest,
    WaveformCacheClearResponse,
    WaveformCacheStatusResponse,
    WaveformGenerateRequest,
    WaveformGenerateResponse,
    WaveformJobResponse,
    WaveformPeakEncoding,
    WaveformResolution,
    WaveformResponse,
)
from ...services import (
    track_source_service,
    waveform_artifact_service,
    waveform_cache_service,
    waveform_identity,
    waveform_job_service,
    waveform_state_service,
)
from ...services.waveform_readiness_service import (
    WaveformRuntimeError,
    generation_blocker,
    resolve_cache_runtime,
)
from ...services.waveform_scheduler import get_scheduler

log = logging.getLogger(__name__)
router = APIRouter(tags=["waveforms"])

_ENCODING = WaveformPeakEncoding(
    type=waveform_artifact_service.ARTIFACT_ENCODING_TYPE,
    scale=waveform_artifact_service.INT16_SCALE,
    rendered_channels=1,
)


def _require_track_source(track_id: int):
    """Resolve a validated source or raise the normal track-level HTTP error."""
    try:
        return track_source_service.validated_track_source(track_id)
    except track_source_service.TrackSourceNotFound:
        raise HTTPException(status_code=404, detail="Track not found")
    except track_source_service.TrackSourcePathRejected:
        raise HTTPException(status_code=403, detail="Track source is outside the selected library")
    except track_source_service.TrackSourceUnavailable:
        raise HTTPException(status_code=404, detail="Track source is unavailable")


def _track_exists(track_id: int) -> bool:
    from ...services import track_service

    return track_service.get_track_by_id(track_id) is not None


def _etag_for(generation_key: str, resolution: str) -> str:
    return f'"{generation_key}-{resolution}"'


def _state_response(track_id: int, status: str, **extra) -> WaveformResponse:
    return WaveformResponse(track_id=track_id, status=status, **extra)


# ---------------------------------------------------------------------------
# GET /api/tracks/{track_id}/waveform
# ---------------------------------------------------------------------------

@router.get("/tracks/{track_id}/waveform", response_model=WaveformResponse)
async def get_waveform(
    track_id: int,
    request: Request,
    response: Response,
    resolution: WaveformResolution = Query(default="player"),
):
    """Return current waveform state, including peaks only when ready.

    This never starts generation. A valid track with no waveform is the
    normal ``not_generated`` state returned with HTTP 200.
    """
    if not _track_exists(track_id):
        raise HTTPException(status_code=404, detail="Track not found")

    try:
        _config, validated_cache = resolve_cache_runtime()
    except WaveformRuntimeError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc

    library_id = track_source_service.library_identity()
    state = waveform_state_service.get_track_state(track_id, library_id=library_id)
    active_job = waveform_job_service.get_active_job_for_track(library_id, track_id)
    job_id = active_job.id if active_job else None

    if state.status is not WaveformArtifactStatus.READY:
        return _state_response(
            track_id,
            state.status.value,
            job_id=job_id,
            error_code=state.last_error_code,
        )

    # Ready: confirm the source still matches before serving cached peaks.
    try:
        snapshot = track_source_service.source_stat_snapshot(track_id)
    except (LookupError, ValueError, OSError):
        return _state_response(track_id, WaveformArtifactStatus.STALE.value, job_id=job_id)
    if not waveform_identity.state_matches_snapshot(state, snapshot):
        return _state_response(track_id, WaveformArtifactStatus.STALE.value, job_id=job_id)

    generation_key = state.cache_key
    if not generation_key:
        return _state_response(track_id, WaveformArtifactStatus.STALE.value, job_id=job_id)

    etag = _etag_for(generation_key, resolution)
    if request.headers.get("if-none-match") == etag:
        # 304 carries no waveform payload at all.
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, no-cache"})

    try:
        document = waveform_artifact_service.read_artifact(validated_cache, generation_key)
        pair_count, peaks = waveform_artifact_service.resolution_payload(document, resolution)
    except waveform_artifact_service.WaveformArtifactError:
        # Missing or corrupt cache: degrade safely, repair state, never regenerate.
        log.info("waveform artifact unusable track_id=%s reason=cache_invalid", track_id)
        _mark_stale(track_id, library_id)
        return _state_response(track_id, WaveformArtifactStatus.STALE.value, job_id=job_id)

    # Record the read for cache-eviction ordering. This is rate-limited inside
    # the service so repeated polling of one track does not amplify into a
    # write per request, and a failure here must never break a read.
    try:
        waveform_job_service.touch_artifact_access(library_id, track_id)
    except Exception:  # pragma: no cover - LRU bookkeeping is best-effort
        log.debug("waveform access bookkeeping skipped track_id=%s", track_id)

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    return WaveformResponse(
        track_id=track_id,
        status=WaveformArtifactStatus.READY.value,
        job_id=job_id,
        schema_version=WAVEFORM_SCHEMA_VERSION,
        algorithm_version=WAVEFORM_ALGORITHM_VERSION,
        resolution=resolution,
        duration_ms=document["audio"]["duration_ms"],
        pair_count=pair_count,
        encoding=_ENCODING,
        peaks=peaks,
        generated_at=state.generated_at,
    )


def _mark_stale(track_id: int, library_id: str) -> None:
    try:
        waveform_state_service.transition_track_state(
            track_id, WaveformArtifactStatus.STALE, library_id=library_id
        )
    except Exception:  # pragma: no cover - state repair must never break a read
        log.debug("waveform state repair skipped track_id=%s", track_id)


# ---------------------------------------------------------------------------
# POST /api/tracks/{track_id}/waveform/generate
# ---------------------------------------------------------------------------

@router.post(
    "/tracks/{track_id}/waveform/generate",
    response_model=WaveformGenerateResponse,
    status_code=202,
)
async def generate_waveform(
    track_id: int,
    response: Response,
    body: WaveformGenerateRequest | None = None,
) -> WaveformGenerateResponse:
    """Explicitly request waveform generation for one track.

    This is the only path that can ever create waveform work. Repeated or
    concurrent requests for the same source generation reuse the existing
    active job instead of launching a second decoder, including when
    ``force=true``.
    """
    request_body = body or WaveformGenerateRequest()
    source = _require_track_source(track_id)

    try:
        _config, validated_cache = resolve_cache_runtime(library_root=source.library_root)
    except WaveformRuntimeError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc

    blocker = generation_blocker(validated_cache)
    if blocker is not None:
        raise HTTPException(status_code=503, detail=blocker.code)

    try:
        snapshot = track_source_service.source_stat_snapshot(track_id)
    except (LookupError, ValueError, OSError) as exc:
        raise HTTPException(status_code=404, detail="Track source is unavailable") from exc

    if snapshot.source_size_bytes > _max_source_size():
        raise HTTPException(status_code=413, detail="WAVEFORM_POLICY_REJECTED")

    generation_key = waveform_identity.compute_generation_key(snapshot)
    scheduler = get_scheduler()
    result = waveform_job_service.submit_generation_job(
        snapshot=snapshot,
        generation_key=generation_key,
        force=request_body.force,
        max_queue_size=scheduler.max_queue_size,
    )

    if result.outcome == "queue_full":
        raise HTTPException(
            status_code=429,
            detail="WAVEFORM_QUEUE_FULL",
            headers={"Retry-After": "5"},
        )
    if result.outcome == "already_ready":
        response.status_code = 200
        return WaveformGenerateResponse(
            track_id=track_id,
            status=WaveformArtifactStatus.READY.value,
            job_id=None,
            deduplicated=False,
        )

    job = result.job
    assert job is not None  # queued/deduplicated always carry a job
    if result.outcome == "queued":
        if not scheduler.enqueue(job.id):
            waveform_job_service.finish_job_unsuccessfully(
                job.id,
                job_status=waveform_job_service.WaveformJobStatus.FAILED,
                track_status=WaveformArtifactStatus.FAILED,
                error_code="WAVEFORM_QUEUE_FULL",
            )
            raise HTTPException(
                status_code=429, detail="WAVEFORM_QUEUE_FULL", headers={"Retry-After": "5"}
            )
        log.info("waveform generation queued job_id=%s track_id=%s", job.id, track_id)
    else:
        log.info("waveform generation deduplicated job_id=%s track_id=%s", job.id, track_id)

    return WaveformGenerateResponse(
        track_id=track_id,
        status=job.status.value,
        job_id=job.id,
        deduplicated=result.deduplicated,
    )


def _max_source_size() -> int:
    from ...core.waveform_limits import MAX_SOURCE_SIZE_BYTES

    return MAX_SOURCE_SIZE_BYTES


# ---------------------------------------------------------------------------
# GET /api/waveform-jobs/{job_id}
# ---------------------------------------------------------------------------

@router.get("/waveform-jobs/{job_id}", response_model=WaveformJobResponse)
async def get_waveform_job(job_id: str) -> WaveformJobResponse:
    """Return privacy-safe status for one waveform job."""
    job = waveform_job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Waveform job not found")
    return _job_response(job)


# ---------------------------------------------------------------------------
# DELETE /api/waveform-jobs/{job_id}
# ---------------------------------------------------------------------------

@router.delete("/waveform-jobs/{job_id}", response_model=WaveformJobResponse)
async def cancel_waveform_job(job_id: str) -> WaveformJobResponse:
    """Best-effort cancellation.

    A queued job is finished immediately so it can never start. A processing
    job has its cancellation token signalled, letting W2's supervisor
    terminate the child process group. A job that already finished is an
    idempotent no-op and never loses its published artifact.
    """
    job = waveform_job_service.request_cancellation(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Waveform job not found")
    get_scheduler().signal_cancel(job_id)
    log.info("waveform cancellation requested job_id=%s status=%s", job_id, job.status.value)
    return _job_response(job)


def _job_response(job) -> WaveformJobResponse:
    return WaveformJobResponse(
        job_id=job.id,
        track_id=job.track_id,
        status=job.status.value,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        cancel_requested=job.cancel_requested,
        error_code=job.error_code,
    )


# ---------------------------------------------------------------------------
# GET /api/waveform-cache
# ---------------------------------------------------------------------------

@router.get("/waveform-cache", response_model=WaveformCacheStatusResponse)
async def get_waveform_cache_status() -> WaveformCacheStatusResponse:
    """Report the cache footprint and preview what a clear would remove.

    Read-only: this counts files and bytes under the validated cache root and
    never deletes, generates, decodes, or hashes anything.
    """
    try:
        config, validated_cache = resolve_cache_runtime()
    except WaveformRuntimeError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc

    status = waveform_cache_service.cache_status(
        validated_cache, max_cache_bytes=config.max_cache_bytes
    )
    preview = waveform_cache_service.preview_clear_cache(validated_cache)
    return WaveformCacheStatusResponse(
        **status, ready_track_count=preview.ready_track_count  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# POST /api/waveform-cache/clear
# ---------------------------------------------------------------------------

@router.post("/waveform-cache/clear", response_model=WaveformCacheClearResponse)
async def clear_waveform_cache(
    body: WaveformCacheClearRequest | None = None,
) -> WaveformCacheClearResponse:
    """Delete every CrateIQ-owned waveform cache file, on explicit confirmation.

    Requires ``{"confirm": true}``; an unconfirmed request is rejected and
    deletes nothing. The waveform cache is derived, disposable data: clearing
    it cannot affect source audio, tags, BPM/key/cue values, playlists,
    crates, review state, or any DJ database. Every affected track simply
    returns to a non-ready state until a waveform is explicitly regenerated.
    """
    request_body = body or WaveformCacheClearRequest()
    if not request_body.confirm:
        raise HTTPException(status_code=400, detail="WAVEFORM_CACHE_CLEAR_NOT_CONFIRMED")

    try:
        config, validated_cache = resolve_cache_runtime()
    except WaveformRuntimeError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc

    outcome = await waveform_cache_service.clear_cache_locked(validated_cache)
    status = waveform_cache_service.cache_status(
        validated_cache, max_cache_bytes=config.max_cache_bytes
    )
    log.info(
        "waveform cache clear confirmed removed_files=%d reset_track_states=%d",
        outcome.removed_files, outcome.reset_track_states,
    )
    return WaveformCacheClearResponse(
        removed_files=outcome.removed_files,
        freed_bytes=outcome.freed_bytes,
        reset_track_states=outcome.reset_track_states,
        remaining_files=outcome.remaining_files,
        current_cache_bytes=int(status["current_cache_bytes"]),  # type: ignore[arg-type]
    )
