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
    decision: DuplicateDecision = "unresolved"
    note: str = ""
    reviewed_at: str | None = None


class DuplicateReviewGroup(BaseModel):
    group_id: str
    reason: str
    confidence: Literal["high", "medium", "low"]
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
