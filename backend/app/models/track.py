"""
Track — internal data model for a pipeline library entry.

Maps 1-to-1 with a row in the pipeline's `tracks` table.
All fields use simple Python types; the model is decoupled from both
the DB layer and the API/Pydantic layer.

The `issues` field is computed at query time — it is not stored in the DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from modules.filename_parse import issue_flags_for_metadata


@dataclass
class Track:
    id:             int
    filepath:       str
    filename:       str
    artist:         Optional[str]
    title:          Optional[str]
    genre:          Optional[str]
    bpm:            Optional[float]
    key_musical:    Optional[str]
    key_camelot:    Optional[str]
    duration_sec:   Optional[float]
    bitrate_kbps:   Optional[int]
    filesize_bytes: Optional[int]
    status:         str
    error_msg:      Optional[str]
    processed_at:   Optional[str]
    pipeline_ver:   Optional[str]
    quality_tier:   Optional[str]
    parse_confidence: Optional[str]
    storage_zone:   Optional[str] = None
    issues:         List[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: "sqlite3.Row") -> "Track":  # noqa: F821
        keys = row.keys()

        def _get(k: str, default=None):
            return row[k] if k in keys else default

        t = cls(
            id=row["id"],
            filepath=row["filepath"],
            filename=row["filename"],
            artist=_get("artist"),
            title=_get("title"),
            genre=_get("genre"),
            bpm=_get("bpm"),
            key_musical=_get("key_musical"),
            key_camelot=_get("key_camelot"),
            duration_sec=_get("duration_sec"),
            bitrate_kbps=_get("bitrate_kbps"),
            filesize_bytes=_get("filesize_bytes"),
            status=row["status"],
            error_msg=_get("error_msg"),
            processed_at=_get("processed_at"),
            pipeline_ver=_get("pipeline_ver"),
            quality_tier=_get("quality_tier"),
            parse_confidence=_get("parse_confidence"),
            storage_zone=_get("storage_zone"),
        )
        t.issues = _compute_issues(t)
        return t


def _compute_issues(t: Track) -> List[str]:
    """Derive issue flags from field values — no additional DB queries."""
    issues: List[str] = []
    if t.bpm is None:
        issues.append("missing_bpm")
    if t.key_camelot is None and t.key_musical is None:
        issues.append("missing_key")
    if not (t.artist or "").strip():
        issues.append("missing_artist")
    if not (t.title or "").strip():
        issues.append("missing_title")
    if t.quality_tier == "LOW":
        issues.append("low_quality")
    if t.status == "error":
        issues.append("error")
    elif t.status == "needs_review":
        issues.append("needs_review")
    issues.extend(
        issue_flags_for_metadata(
            artist=t.artist,
            title=t.title,
            parse_confidence=t.parse_confidence,
        )
    )
    return issues
