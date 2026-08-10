"""Plan-first duplicate resolution contract: propose, never apply.

Consumes only the latest saved duplicate-review snapshot plus the existing
human decisions already recorded against it in ``duplicate_review_service``.
Never deletes, moves, renames, quarantines, or writes tags/files. There is no
apply/execute endpoint for this contract; see
``docs/architecture/DUPLICATE_RESOLUTION_SPEC.md`` for the future controlled
apply design this plan exists to prepare for.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DuplicateResolutionAction = Literal[
    "keep", "candidate_for_reversible_resolution", "no_action", "review_required"
]


class DuplicateResolutionIdentityEvidence(BaseModel):
    """Evidence must be labeled truthfully -- never claim a full hash when only a prefix is known."""

    type: Literal["checksum_prefix", "none"] = "none"
    value: str | None = None
    note: str = ""


class DuplicateResolutionObservedStat(BaseModel):
    size_bytes: int | None = None
    mtime_ns: str | None = None


class DuplicateResolutionExecutionRequirements(BaseModel):
    """What a future controlled apply phase must prove before mutating this track."""

    source_relative_path: str
    track_id: int
    identity_evidence: DuplicateResolutionIdentityEvidence
    observed_at_plan_time: DuplicateResolutionObservedStat
    proposed_destination_strategy: str
    collision_check_required: bool = True
    backup_required: bool = True
    restore_preconditions: list[str] = Field(default_factory=list)
    operation_ledger_linkage: str


class DuplicateResolutionItem(BaseModel):
    action_id: str
    track_id: int
    filename: str
    relative_path: str | None = None
    action: DuplicateResolutionAction
    blockers: list[str] = Field(default_factory=list)
    execution_requirements: DuplicateResolutionExecutionRequirements | None = None


class DuplicateResolutionGroup(BaseModel):
    group_id: str
    status: Literal["ready", "blocked"]
    blockers: list[str] = Field(default_factory=list)
    keeper_track_id: int | None = None
    match_basis: str
    checksum_prefix: str | None = None
    items: list[DuplicateResolutionItem] = Field(default_factory=list)


class DuplicateResolutionSummary(BaseModel):
    groups: int = 0
    ready_groups: int = 0
    blocked_groups: int = 0
    keep: int = 0
    candidate_for_reversible_resolution: int = 0
    no_action: int = 0
    review_required: int = 0


class DuplicateResolutionPlanResponse(BaseModel):
    generated_at: str | None = None
    latest_preview_at: str | None = None
    source: str | None = None
    apply_supported: bool = False
    groups: list[DuplicateResolutionGroup] = Field(default_factory=list)
    summary: DuplicateResolutionSummary
    safety: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    message: str
