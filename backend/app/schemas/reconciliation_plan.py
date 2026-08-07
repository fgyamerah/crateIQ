"""Plan-first reconciliation proposal response shape. Propose and validate only — never apply."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ReconciliationPlanProposeResponse(BaseModel):
    generated_at: str | None = None
    plan_artifact: str | None = None
    apply_supported: bool = False
    planned_action_summary: dict[str, int] = Field(default_factory=dict)
    planned_actions: list[dict[str, Any]] = Field(default_factory=list)
    audit_summary: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    message: str
