"""Safe, database-only duplicate review response shapes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


DuplicateDecision = Literal["keep", "ignore", "review_later", "unresolved"]


class DuplicateReviewItem(BaseModel):
    track_id: int
    filename: str
    title: str | None = None
    artist: str | None = None
    relative_path: str | None = None
    size_bytes: int | None = None
    genre: str | None = None
    bpm: float | None = None
    key_camelot: str | None = None
    key_musical: str | None = None
    duration_sec: float | None = None
    format: str | None = None
    missing_metadata: list[str] = Field(default_factory=list)
    copy_marker: bool = False
    decision: DuplicateDecision = "unresolved"
    note: str = ""
    reviewed_at: str | None = None


class DuplicateKeeperRecommendation(BaseModel):
    """Advisory-only keeper suggestion. Never authorizes removing another item."""

    track_id: int | None = None
    reason_code: str = "insufficient_evidence"
    evidence: list[str] = Field(default_factory=list)


class DuplicateReviewGroup(BaseModel):
    group_id: str
    reason: str
    confidence: Literal["high", "medium", "low"]
    match_basis: str = "unknown"
    checksum_prefix: str | None = None
    recommendation: DuplicateKeeperRecommendation = Field(default_factory=DuplicateKeeperRecommendation)
    items: list[DuplicateReviewItem] = Field(default_factory=list)


class DuplicateReviewSummary(BaseModel):
    groups: int = 0
    candidates: int = 0
    unresolved: int = 0
    keep: int = 0
    ignore: int = 0
    review_later: int = 0


class DuplicateReviewResponse(BaseModel):
    summary: DuplicateReviewSummary
    groups: list[DuplicateReviewGroup] = Field(default_factory=list)
    safety: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    latest_preview_at: str | None = None
    source: str | None = None
    message: str | None = None


class DuplicateReviewDecisionUpdate(BaseModel):
    decision: DuplicateDecision
    note: str = Field(default="", max_length=1000)
