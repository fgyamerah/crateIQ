"""Privacy-safe waveform capability schemas used by readiness APIs."""
from __future__ import annotations

from typing import Literal

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
