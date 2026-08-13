"""Safe, read-only orphan / stale-path / quarantine finding response shapes."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FindingType = Literal["indexed_missing_file", "untracked_file", "stale_path", "path_candidate"]

ReferenceArtifactType = Literal[
    "cue_point", "playlist_track", "crate_track", "repair_queue_item",
    "enrichment_review_snapshot", "beets_review_snapshot", "duplicate_review_snapshot",
    "quality_review_snapshot", "quality_review_finding", "track_review",
    "genre_review_snapshot", "field_provenance", "queue_entry", "bpm_anomaly",
    "bpm_retry_pause", "fingerprint_cache", "waveform_track_state", "waveform_job",
    "historical_reference", "tag_write_operation", "export_file",
]
ReferenceClassification = Literal["A", "B", "C", "D", "E"]
ReferenceDisposition = Literal["correct", "regenerate", "mark_unresolvable", "ignore", "manual_review"]
ReferenceConfidence = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]


class FindingPathSide(BaseModel):
    relative_path: str | None = None
    filename: str | None = None
    track_id: int | None = None
    status: str | None = None
    stage: str | None = None
    size_bytes: int | None = None


class ReconciliationFinding(BaseModel):
    finding_id: str
    finding_type: FindingType
    summary: str
    db_side: FindingPathSide | None = None
    filesystem_side: FindingPathSide | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class ReconciliationFindingsSummary(BaseModel):
    indexed_missing_file: int = 0
    untracked_file: int = 0
    stale_path: int = 0
    path_candidate: int = 0


class ReconciliationFindingsResponse(BaseModel):
    summary: ReconciliationFindingsSummary
    findings: list[ReconciliationFinding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: str | None = None
    message: str | None = None


class ReferenceArtifactFinding(BaseModel):
    """A bounded, read-only observation of one stale secondary reference."""

    finding_id: str
    artifact_type: ReferenceArtifactType
    artifact_owner: str
    artifact_identifier: str
    reference_field: str
    stale_value: str | int
    candidate_replacement: str | int | None = None
    evidence_source: str
    classification: ReferenceClassification
    confidence: ReferenceConfidence | None = None
    blockers: list[str] = Field(default_factory=list)
    recommended_disposition: ReferenceDisposition
    generated_at: str
    revalidated_at: str | None = None


class ReferenceFindingsSummary(BaseModel):
    total: int = 0
    category_a: int = 0
    category_b: int = 0
    category_c: int = 0
    category_d: int = 0
    category_e: int = 0


class ReferenceFindingsResponse(BaseModel):
    summary: ReferenceFindingsSummary = Field(default_factory=ReferenceFindingsSummary)
    findings: list[ReferenceArtifactFinding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: str
    message: str


class QuarantineItem(BaseModel):
    relative_path: str
    filename: str
    size_bytes: int | None = None
    original_relative_path: str | None = None
    reason: str | None = None
    operation_id: str | None = None
    quarantined_at: str | None = None
    restore_supported: bool = False


class QuarantineListingResponse(BaseModel):
    supported: bool = True
    items: list[QuarantineItem] = Field(default_factory=list)
    message: str | None = None
