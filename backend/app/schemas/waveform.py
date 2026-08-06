"""Privacy-safe waveform capability schemas used by readiness APIs."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel


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
