"""Plan-only schemas for reference-artifact reconciliation (Stage 2)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ReferencePlanValidateRequest(BaseModel):
    plan_path: str | None = None
    latest: bool = False


class ReferencePlanProposeResponse(BaseModel):
    plan_id: str
    generated_at: str
    plan_artifact: str
    finding_summary: dict[str, Any] = Field(default_factory=dict)
    planned_actions: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    message: str


class ReferencePlanValidateResponse(BaseModel):
    generated_at: str
    plan_artifact: str
    plan_id: str | None = None
    total_actions: int = 0
    valid_actions: int = 0
    invalid_actions: int = 0
    non_executable_actions: int = 0
    reasons: dict[str, int] = Field(default_factory=dict)
    validation_records: list[dict[str, Any]] = Field(default_factory=list)
    message: str
