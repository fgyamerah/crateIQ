"""Internal waveform foundation models.

These models describe operational state only.  They do not decode audio,
represent peak data, or expose filesystem paths.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


WAVEFORM_SCHEMA_VERSION = 1
WAVEFORM_ALGORITHM_VERSION = "mono-minmax-s16-v1"


class WaveformArtifactStatus(str, Enum):
    NOT_GENERATED = "not_generated"
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    STALE = "stale"
    CANCELLED = "cancelled"


class WaveformJobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WaveformCapabilityStatus(str, Enum):
    DISABLED = "disabled"
    MISCONFIGURED = "misconfigured"
    CACHE_UNAVAILABLE = "cache_unavailable"
    EXTRACTOR_UNAVAILABLE = "extractor_unavailable"
    DETECTED = "detected"
    READY = "ready"


ARTIFACT_TRANSITIONS: dict[WaveformArtifactStatus, frozenset[WaveformArtifactStatus]] = {
    WaveformArtifactStatus.NOT_GENERATED: frozenset({WaveformArtifactStatus.QUEUED}),
    WaveformArtifactStatus.QUEUED: frozenset({
        WaveformArtifactStatus.PROCESSING,
        WaveformArtifactStatus.FAILED,
        WaveformArtifactStatus.CANCELLED,
    }),
    WaveformArtifactStatus.PROCESSING: frozenset({
        WaveformArtifactStatus.READY,
        WaveformArtifactStatus.FAILED,
        WaveformArtifactStatus.UNSUPPORTED,
        WaveformArtifactStatus.CANCELLED,
    }),
    WaveformArtifactStatus.READY: frozenset({WaveformArtifactStatus.STALE}),
    WaveformArtifactStatus.STALE: frozenset({WaveformArtifactStatus.QUEUED}),
    WaveformArtifactStatus.FAILED: frozenset({WaveformArtifactStatus.QUEUED}),
    WaveformArtifactStatus.UNSUPPORTED: frozenset({WaveformArtifactStatus.STALE}),
    WaveformArtifactStatus.CANCELLED: frozenset({WaveformArtifactStatus.QUEUED}),
}


JOB_TRANSITIONS: dict[WaveformJobStatus, frozenset[WaveformJobStatus]] = {
    WaveformJobStatus.QUEUED: frozenset({
        WaveformJobStatus.PROCESSING,
        WaveformJobStatus.FAILED,
        WaveformJobStatus.CANCELLED,
    }),
    WaveformJobStatus.PROCESSING: frozenset({
        WaveformJobStatus.SUCCEEDED,
        WaveformJobStatus.FAILED,
        WaveformJobStatus.CANCELLED,
    }),
    WaveformJobStatus.SUCCEEDED: frozenset(),
    WaveformJobStatus.FAILED: frozenset(),
    WaveformJobStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class SourceStatSnapshot:
    """Cheap, private source identity captured after root validation."""

    library_id: str
    track_id: int
    source_size_bytes: int
    source_mtime_ns: int
    source_ctime_ns: int
    source_device: int
    source_inode: int


@dataclass(frozen=True)
class WaveformTrackState:
    library_id: str
    track_id: int
    status: WaveformArtifactStatus
    schema_version: int
    algorithm_version: str
    source_size_bytes: Optional[int] = None
    source_mtime_ns: Optional[int] = None
    source_ctime_ns: Optional[int] = None
    source_device: Optional[int] = None
    source_inode: Optional[int] = None
    source_sha256: Optional[str] = None
    cache_key: Optional[str] = None
    generated_at: Optional[str] = None
    last_error_code: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class WaveformJobRecord:
    id: str
    library_id: str
    track_id: int
    status: WaveformJobStatus
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    cancel_requested: bool = False
    error_code: Optional[str] = None
