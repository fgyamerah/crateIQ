from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

Severity = Literal['blocker', 'warning', 'optional']


class ReadinessReason(BaseModel):
    code: str
    severity: Severity
    message: str
    route: str | None = None


class LibraryReadinessResponse(BaseModel):
    total_tracks: int = 0
    ready: bool = False
    blockers: list[ReadinessReason] = Field(default_factory=list)
    warnings: list[ReadinessReason] = Field(default_factory=list)
    optional: list[ReadinessReason] = Field(default_factory=list)
    coverage: dict[str, int] = Field(default_factory=dict)
    message: str = ''
