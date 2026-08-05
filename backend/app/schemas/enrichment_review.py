from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

Decision = Literal['pending', 'applied', 'ignored', 'review_later']

class SuggestionUpdate(BaseModel):
    decision: Decision = 'pending'
    note: str = Field(default='', max_length=1000)
    selected_fields: dict[str, str] = Field(default_factory=dict)

class ApplyItem(BaseModel):
    track_id: int = Field(ge=1)
    suggestion_id: str = Field(min_length=1, max_length=128)
    fields: dict[str, str] = Field(default_factory=dict)

class ApplyRequest(BaseModel):
    confirm: bool = False
    items: list[ApplyItem] = Field(default_factory=list, max_length=100)

class ReviewResponse(BaseModel):
    summary: dict = Field(default_factory=dict)
    items: list[dict] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    safety: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    latest_preview_at: str | None = None
    message: str | None = None

class ApplyResult(BaseModel):
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    warnings: list[str] = Field(default_factory=list)
    review: ReviewResponse
