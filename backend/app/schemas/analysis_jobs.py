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
    relative_path: Optional[str] = None
    artist: Optional[str] = None
    title: Optional[str] = None
    genre: Optional[str] = None
    bpm: Optional[float] = None
    key_camelot: Optional[str] = None
    key_musical: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)


class DuplicateCandidateItem(BaseModel):
    track_id: int
    filename: str
    title: Optional[str] = None
    artist: Optional[str] = None
    relative_path: Optional[str] = None
    size_bytes: Optional[int] = None


class DuplicateCandidateGroup(BaseModel):
    group_id: str
    reason: str
    confidence: Literal["high", "medium", "low"]
    items: list[DuplicateCandidateItem] = Field(default_factory=list)


class QualityProbeResult(BaseModel):
    track_id: int
    filename: str
    relative_path: Optional[str] = None
    status: Literal["probe_ok", "missing_bitrate", "unreadable", "unsupported_format", "probe_error"]
    container: Optional[str] = None
    codec: Optional[str] = None
    duration_sec: Optional[float] = None
    bitrate_kbps: Optional[int] = None
    sample_rate_hz: Optional[int] = None
    channels: Optional[int] = None
    file_size_bytes: Optional[int] = None


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
    preview_only: bool = False
    summary: dict[str, int] | None = None
    groups: list[DuplicateCandidateGroup] = Field(default_factory=list)
    quality_probes: list[QualityProbeResult] = Field(default_factory=list)
    next_step: Optional[str] = None


AnalysisOperationStatus = Literal["running", "completed", "failed", "cancelled"]

# A derived, presentation-safe outcome computed from status + track-level
# counts (see analysis_operations_service.derive_outcome). `status` alone
# cannot distinguish a clean completion from one where every track failed,
# or one where tracks only succeeded via the FFmpeg BPM-recovery fallback --
# this is additive so existing `status` consumers are unaffected.
AnalysisOperationOutcome = Literal[
    "running", "complete", "completed_with_warnings", "completed_with_errors",
    "cancelled", "failed",
]


class AnalysisOperation(BaseModel):
    """A persisted, app-owned record of one explicit, confirmed analysis run.

    Lives in the backend's own jobs.db -- never in the trusted pipeline
    processed.db. Candidate previews are never persisted; only a confirmed
    run that began work creates one of these.
    """
    id: str
    job_type: Literal["bpm_analysis", "key_analysis"]
    mode: str
    status: AnalysisOperationStatus
    outcome: AnalysisOperationOutcome
    scope_limit: int
    eligible_total: int
    considered: int
    processed: int
    succeeded: int
    skipped: int
    failed: int
    recovered: int = 0
    remaining_missing: Optional[int] = None
    cancel_requested: bool
    error_reason: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class AnalysisJobHistoryResponse(BaseModel):
    history: list[AnalysisOperation] = Field(default_factory=list)
    message: str


class BpmAnalysisRunRequest(BaseModel):
    confirm: bool = False
    limit: int = Field(default=10, ge=1, le=50)


class BpmAnalysisRunResult(BaseModel):
    job_type: Literal["bpm_analysis"]
    analyzed: int
    updated: int
    skipped: int
    failed: int
    recovered: int = 0
    remaining_missing_bpm: int
    warnings: list[str] = Field(default_factory=list)
    results: list[AnalysisJobCandidate] = Field(default_factory=list)
    operation_id: Optional[str] = None
    cancelled: bool = False
    outcome: AnalysisOperationOutcome = "complete"


class KeyAnalysisRunRequest(BpmAnalysisRunRequest):
    pass


class KeyAnalysisRunResult(BaseModel):
    job_type: Literal["key_analysis"]
    analyzed: int
    updated: int
    skipped: int
    failed: int
    remaining_missing_key: int
    warnings: list[str] = Field(default_factory=list)
    results: list[AnalysisJobCandidate] = Field(default_factory=list)
    operation_id: Optional[str] = None
    cancelled: bool = False
