"""Read-only Analysis Jobs catalog and candidate-preview response shapes."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


AnalysisJobType = Literal[
    "mixed_in_key_coverage",
    "bpm_analysis",
    "key_analysis",
    "beets_enrichment",
    "duplicate_detection",
    "audio_quality_probe",
]


class AnalysisJobDefinition(BaseModel):
    type: AnalysisJobType
    label: str
    status: Literal["ready", "missing_tool", "coming_soon", "disabled"]
    required_tools: list[str] = Field(default_factory=list)
    required_source: Optional[str] = None
    candidate_count: int
    enabled: bool
    default_enabled: bool = False
    runner_implemented: bool
    write_behavior: str
    safety: list[str]
    message: str


class AnalysisJobCandidate(BaseModel):
    track_id: int
    filename: str
    artist: Optional[str] = None
    title: Optional[str] = None
    genre: Optional[str] = None
    bpm: Optional[float] = None
    key_camelot: Optional[str] = None
    key_musical: Optional[str] = None


class AnalysisJobListResponse(BaseModel):
    jobs: list[AnalysisJobDefinition]


class AnalysisJobPreview(BaseModel):
    job: AnalysisJobDefinition
    total_tracks: int
    candidate_count: int
    samples: list[AnalysisJobCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    expected_write_behavior: str
    runner_implemented: bool


class AnalysisJobHistoryResponse(BaseModel):
    history: list[dict] = Field(default_factory=list)
    message: str
