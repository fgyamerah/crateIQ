"""
Runtime readiness route.

  GET /api/runtime/readiness — read-only local-runtime preflight report

The endpoint only inspects configuration, paths, and binary availability.
It never mutates library data, never runs pipeline commands or jobs, and
never returns secret values.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...core.preflight import run_preflight
from ...schemas.waveform import WaveformCapability

router = APIRouter(tags=["runtime"])


class ReadinessCheck(BaseModel):
    name: str
    status: str  # pass | warn | fail
    message: str
    required: bool
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ReadinessResponse(BaseModel):
    status: str  # ready | degraded | not_ready
    checks: List[ReadinessCheck]
    waveform: WaveformCapability


@router.get("/runtime/readiness", response_model=ReadinessResponse)
async def readiness() -> ReadinessResponse:
    report = run_preflight()
    return ReadinessResponse(**report)
