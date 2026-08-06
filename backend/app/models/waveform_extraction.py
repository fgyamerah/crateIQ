"""Internal W2 extraction models: error taxonomy, probe/result shapes, cancellation.

These describe the internal extraction engine boundary only. Nothing here is
serialized by a public API, and no field carries a source, cache, or
executable path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class WaveformExtractionErrorCode(str, Enum):
    EXTRACTOR_UNAVAILABLE = "extractor_unavailable"
    PROBE_FAILURE = "probe_failure"
    INVALID_PROBE = "invalid_probe"
    UNSUPPORTED_CODEC = "unsupported_codec"
    SOURCE_POLICY_REJECTED = "source_policy_rejected"
    SOURCE_CHANGED = "source_changed"
    DECODE_FAILURE = "decode_failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    RESOURCE_POLICY_REJECTED = "resource_policy_rejected"
    INVALID_PCM = "invalid_pcm"
    PROCESS_LAUNCH_FAILURE = "process_launch_failure"


class WaveformExtractionError(Exception):
    """Raised for every W2 extraction failure branch.

    ``detail`` is for internal/DEBUG use only (may include sanitized stderr
    categories); callers must never surface it through a public response.
    """

    def __init__(self, code: WaveformExtractionErrorCode, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class CancellationToken:
    """Cooperative cancellation signal observable across probe/decode/accumulate."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise WaveformExtractionError(WaveformExtractionErrorCode.CANCELLED, "extraction was cancelled")


@dataclass(frozen=True)
class ProbeResult:
    """Bounded, validated subset of ffprobe output. No tags, no unrelated metadata."""

    duration_seconds: float | None
    source_channels: int
    source_sample_rate_hz: int
    codec_name: str | None


@dataclass(frozen=True)
class WaveformExtractionResult:
    """Derived-only result. No source/library/cache/executable path fields."""

    duration_ms: int
    source_channels: int
    source_sample_rate_hz: int
    analysis_sample_rate_hz: int
    encoding: str
    resolutions: Mapping[str, list[int]] = field(default_factory=dict)
