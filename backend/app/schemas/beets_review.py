"""Safe local-index Beets enrichment review and selected-field apply shapes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


BeetsReviewDecision = Literal["pending", "applied", "ignored", "review_later"]


class BeetsReviewItem(BaseModel):
    track_id: int
    filename: str
    relative_path: str | None = None
    current_fields: dict[str, str | None] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    allowed_fields: list[str] = Field(default_factory=list)
    selected_fields: dict[str, str] = Field(default_factory=dict)
    decision: BeetsReviewDecision = "pending"
    note: str = ""
    updated_at: str | None = None


class BeetsReviewSummary(BaseModel):
    candidates: int = 0
    pending: int = 0
    applied: int = 0
    ignored: int = 0
    review_later: int = 0
    fields_selected: int = 0


class BeetsReviewResponse(BaseModel):
    summary: BeetsReviewSummary
    items: list[BeetsReviewItem] = Field(default_factory=list)
    safety: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    latest_preview_at: str | None = None
    source: str | None = None
    message: str | None = None


class BeetsReviewTrackUpdate(BaseModel):
    decision: BeetsReviewDecision = "pending"
    note: str = Field(default="", max_length=1000)
    selected_fields: dict[str, str] = Field(default_factory=dict)


class BeetsApplyItem(BaseModel):
    track_id: int = Field(ge=1)
    fields: dict[str, str] = Field(default_factory=dict)


class BeetsApplyRequest(BaseModel):
    confirm: bool = False
    items: list[BeetsApplyItem] = Field(default_factory=list, max_length=100)


class BeetsApplyResult(BaseModel):
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    warnings: list[str] = Field(default_factory=list)
    review: BeetsReviewResponse
