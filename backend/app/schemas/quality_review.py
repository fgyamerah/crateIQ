"""Safe, database-only audio-quality review response shapes."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


QualityReviewDecision = Literal["reviewed", "ignore", "review_later", "unresolved"]
QualityIssueFlag = Literal[
    "unreadable", "missing_bitrate", "low_bitrate", "missing_duration",
    "unsupported_format", "probe_warning",
]
QualityReviewStatus = Literal[
    "probe_ok", "unreadable", "missing_bitrate", "unsupported_format", "probe_warning",
]
QualityFindingSeverity = Literal["warning", "error"]


class QualityReviewItem(BaseModel):
    finding_key: str | None = None
    track_id: int
    filename: str
    title: str | None = None
    artist: str | None = None
    relative_path: str | None = None
    container: str | None = None
    codec: str | None = None
    duration_sec: float | None = None
    bitrate_kbps: int | None = None
    sample_rate_hz: int | None = None
    channels: int | None = None
    file_size_bytes: int | None = None
    status: QualityReviewStatus | None = None
    flags: list[QualityIssueFlag] = Field(default_factory=list)
    # Durable (non-ffprobe) finding fields -- None/False for ffprobe-snapshot items.
    reason_code: str | None = None
    source: str | None = None
    severity: QualityFindingSeverity | None = None
    blocking: bool = False
    message: str | None = None
    details: dict[str, Any] | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    occurrence_count: int | None = None
    decision: QualityReviewDecision = "unresolved"
    note: str = ""
    reviewed_at: str | None = None
    # True only for an active audio_decode_failed finding whose track
    # currently has an automatic-retry pause. False for every ffprobe item
    # and for every other durable reason_code.
    retry_paused: bool = False


class QualityReviewSummary(BaseModel):
    tracks_checked: int = 0
    findings: int = 0
    unresolved: int = 0
    reviewed: int = 0
    ignored: int = 0
    review_later: int = 0


class QualityReviewResponse(BaseModel):
    summary: QualityReviewSummary
    items: list[QualityReviewItem] = Field(default_factory=list)
    safety: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    latest_preview_at: str | None = None
    source: str | None = None
    low_bitrate_threshold_kbps: int = 192
    message: str | None = None


class QualityReviewDecisionUpdate(BaseModel):
    decision: QualityReviewDecision
    note: str = Field(default="", max_length=1000)
    finding_key: str | None = Field(default=None, max_length=64)
