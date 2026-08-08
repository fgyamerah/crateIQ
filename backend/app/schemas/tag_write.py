from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field

OperationStatus = Literal['previewed', 'running', 'completed', 'failed', 'partially_failed', 'restored']


class TagWritePlanRequest(BaseModel):
    track_ids: list[int] = Field(min_length=1, max_length=50)


class TagWritePlanResponse(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    track_count: int = 0
    changeable_count: int = 0
    no_op_count: int = 0
    blocked_count: int = 0
    additions: int = 0
    replacements: int = 0
    backup_space_estimate_bytes: int = 0
    writable_fields: list[str] = Field(default_factory=list)
    supported_formats: list[str] = Field(default_factory=list)
    message: str = ''


class TagWriteExpectedStat(BaseModel):
    expected_size: int
    expected_mtime_ns: int


class TagWriteApplyRequest(BaseModel):
    track_ids: list[int] = Field(min_length=1, max_length=50)
    expected: dict[int, TagWriteExpectedStat] = Field(default_factory=dict)
    confirm: bool = False


class TagWriteApplyResult(BaseModel):
    operation_id: str
    status: OperationStatus
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[dict[str, Any]] = Field(default_factory=list)


class TagWriteOperation(BaseModel):
    id: str
    status: OperationStatus
    track_count: int = 0
    applied_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    plan: list[dict[str, Any]] = Field(default_factory=list)
    backup_manifest: list[dict[str, Any]] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_reason: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    restored_at: str | None = None


class TagWriteRestoreRequest(BaseModel):
    track_id: int
    confirm: bool = False


class TagWriteRestoreResult(BaseModel):
    track_id: int
    restored: bool
    verified: bool
    relative_path: str
