"""Safe, database-only audio-quality review response shapes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


QualityReviewDecision = Literal["reviewed", "ignore", "review_later", "unresolved"]
QualityIssueFlag = Literal[
    "unreadable", "missing_bitrate", "low_bitrate", "missing_duration",
    "unsupported_format", "probe_warning",
]
QualityReviewStatus = Literal[
    "probe_ok", "unreadable", "missing_bitrate", "unsupported_format", "probe_warning",
]


class QualityReviewItem(BaseModel):
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
    status: QualityReviewStatus
    flags: list[QualityIssueFlag] = Field(default_factory=list)
    decision: QualityReviewDecision = "unresolved"
    note: str = ""
    reviewed_at: str | None = None


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
