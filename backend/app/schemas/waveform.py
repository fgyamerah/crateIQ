"""Privacy-safe waveform capability schemas used by readiness APIs."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class WaveformEngineCapability(BaseModel):
    name: Literal["ffmpeg"]
    detected: bool
    ffmpeg_detected: bool
    ffprobe_detected: bool
    version_verified: bool


class WaveformCapability(BaseModel):
    enabled: bool
    status: Literal[
        "disabled",
        "misconfigured",
        "cache_unavailable",
        "extractor_unavailable",
        "detected",
        "ready",
    ]
    cache_ready: bool
    engine: WaveformEngineCapability
    message: str


# ---------------------------------------------------------------------------
# W3 generation lifecycle
#
# None of these models carry a source path, cache path, library root,
# executable path, raw subprocess output, or content hash.
# ---------------------------------------------------------------------------

WaveformResolution = Literal["compact", "player", "detail"]


class WaveformPeakEncoding(BaseModel):
    type: str
    scale: int
    rendered_channels: int


class WaveformResponse(BaseModel):
    """Read-only waveform state, with peak data only when ``status='ready'``."""

    track_id: int
    status: str
    job_id: Optional[str] = None
    schema_version: Optional[int] = None
    algorithm_version: Optional[str] = None
    resolution: Optional[str] = None
    duration_ms: Optional[int] = None
    pair_count: Optional[int] = None
    encoding: Optional[WaveformPeakEncoding] = None
    peaks: Optional[List[int]] = None
    generated_at: Optional[str] = None
    error_code: Optional[str] = None


class WaveformGenerateRequest(BaseModel):
    force: bool = False


class WaveformGenerateResponse(BaseModel):
    track_id: int
    status: str
    job_id: Optional[str] = None
    deduplicated: bool = False


class WaveformJobResponse(BaseModel):
    job_id: str
    track_id: int
    status: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    cancel_requested: bool = False
    error_code: Optional[str] = None


# ---------------------------------------------------------------------------
# W6 cache lifecycle
#
# Counts and byte totals only. No cache path, library root, filename, track
# title, or content hash appears in any of these models.
# ---------------------------------------------------------------------------


class WaveformCacheStatusResponse(BaseModel):
    """Current cache footprint plus a preview of what a clear would remove."""

    current_cache_bytes: int
    max_cache_bytes: int
    artifact_count: int
    temp_count: int
    superseded_count: int
    over_limit: bool
    algorithm_version: str
    ready_track_count: int


class WaveformCacheClearRequest(BaseModel):
    """Explicit confirmation is required; there is no implicit clear."""

    confirm: bool = False


class WaveformCacheClearResponse(BaseModel):
    removed_files: int
    freed_bytes: int
    reset_track_states: int
    remaining_files: int
    current_cache_bytes: int


# ---------------------------------------------------------------------------
# Bulk "Generate missing waveforms" (Waveform Jobs Stage 2)
#
# Read-only preview plus a persisted, app-owned parent-operation history.
# Never carries a source path, cache path, or content hash.
# ---------------------------------------------------------------------------


class WaveformBulkPreviewResponse(BaseModel):
    """Truthful, side-effect-free counts. Never enqueues a job."""

    total_tracks: int
    ready: int
    missing: int
    generating: int
    failed: int
    unsupported: int
    eligible_to_generate: int


class WaveformBulkStartResponse(BaseModel):
    id: str
    total_tracks: int
    eligible_total: int


WaveformBulkOperationStatus = Literal["running", "completed", "failed", "cancelled"]


class WaveformBulkOperation(BaseModel):
    """A persisted, app-owned record of one explicit, confirmed bulk run.

    Lives in the backend's own jobs.db -- never in the trusted pipeline
    processed.db. Read-only previews are never persisted; only a confirmed
    run that began work creates one of these.
    """

    id: str
    operation_type: Literal["generate_missing"]
    status: WaveformBulkOperationStatus
    total_tracks: int
    eligible_total: int
    processed: int
    generated: int
    skipped: int
    failed: int
    remaining_missing: Optional[int] = None
    cancel_requested: bool
    error_reason: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class WaveformBulkHistoryResponse(BaseModel):
    history: List[WaveformBulkOperation] = Field(default_factory=list)
