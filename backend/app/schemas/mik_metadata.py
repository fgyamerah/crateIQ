"""Response shapes for the explicit MIK-compatible metadata workflow."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class MikCoverageSummary(BaseModel):
    total_tracks: int
    with_bpm: int
    with_key: int
    with_camelot: int
    with_cues: int
    trusted_bpm: int
    trusted_key: int
    missing_bpm: int
    missing_key: int
    fallback_bpm_candidates: int
    fallback_key_candidates: int


class MikFinding(BaseModel):
    track_id: int
    filename: str
    bpm: Optional[float] = None
    key_camelot: Optional[str] = None
    key_musical: Optional[str] = None
    source: Literal["mixed_in_key", "mik_compatible_tag", "existing_metadata", "filename_hint", "unknown"]
    trusted: bool


class MikCoverageResponse(BaseModel):
    summary: MikCoverageSummary
    samples: list[MikFinding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cue_support: Literal["available", "unavailable", "not_implemented"]
    write_behavior: Literal["crateiq_db_only"]


class MikImportResponse(MikCoverageResponse):
    imported_count: int
    unchanged_count: int
    skipped_count: int
