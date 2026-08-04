"""
Pydantic schemas for the export API.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Literal

from pydantic import BaseModel, Field


class ExportRunRequest(BaseModel):
    dry_run:          bool = Field(False, description="Preview only — no files written")
    skip_m3u:         bool = Field(False, description="Skip M3U playlist generation")
    force_xml:        bool = Field(
        False,
        description="Generate Rekordbox XML. NOT recommended when using Mixed In Key.",
    )
    recover_missing:  bool = Field(
        False,
        description="Run aubio/keyfinder inline for tracks missing BPM/key. Slower.",
    )


class ExportRunResponse(BaseModel):
    job_id:  str
    message: str


CrateExportFormat = Literal["csv", "json", "m3u", "m3u8"]
CratePathMode = Literal["filename", "relative", "absolute"]
CrateLineEndings = Literal["lf", "crlf"]


class CrateExportRequest(BaseModel):
    format: CrateExportFormat = "m3u8"
    path_mode: CratePathMode = "filename"
    include_metadata: bool = True
    line_endings: CrateLineEndings = "lf"


class CrateExportPreview(BaseModel):
    crate_id: int
    crate_name: str
    format: CrateExportFormat
    path_mode: CratePathMode
    track_count: int
    warnings: List[str]
    content: str


class CrateExportResult(CrateExportPreview):
    output_path: str


# ---------------------------------------------------------------------------
# Validation response
# ---------------------------------------------------------------------------

class ExclusionCategory(str):
    MISSING_ANALYSIS = "MISSING_ANALYSIS"
    MISSING_METADATA = "MISSING_METADATA"
    STALE_DB         = "STALE_DB"
    JUNK_PLACEHOLDER = "JUNK_PLACEHOLDER"
    BAD_PATH         = "BAD_PATH"
    OTHER            = "OTHER"


class ExcludedTrack(BaseModel):
    filepath:    str
    filename:    str
    artist:      Optional[str] = None
    title:       Optional[str] = None
    bpm:         Optional[float] = None
    key_camelot: Optional[str] = None
    genre:       Optional[str] = None
    reasons:     List[str]
    category:    str   # primary category from CATEGORY constants above


class ExportWarning(BaseModel):
    level:   str   # "info" | "warning" | "error"
    message: str


class ValidationStats(BaseModel):
    total_scanned:    int
    valid_count:      int
    invalid_count:    int
    missing_analysis: int   # tracks excluded solely due to missing BPM/key
    missing_metadata: int   # tracks excluded due to missing artist/title/genre
    stale_db:         int   # tracks pointing to files not on disk
    junk:             int   # placeholder / junk filenames
    other:            int   # anything else
    by_category:      Dict[str, int]


class ValidateResponse(BaseModel):
    stats:     ValidationStats
    warnings:  List[ExportWarning]
    excluded:  List[ExcludedTrack]   # up to 500
    truncated: bool = False           # True if more than 500 excluded tracks
    output_paths: Dict[str, str]      # e.g. {"m3u": "/mnt/.../M3U", "xml": "/mnt/.../XML"}
