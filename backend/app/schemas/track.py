"""
Pydantic schemas for the tracks API.

TrackSummary  — lightweight row shape used in list responses
TrackDetail   — full field set for single-track responses
TrackStats    — aggregate counts for the stats endpoint
TrackIssueItem — single item in the issues list response
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from ..models.track import Track


def _recommended_issue_route(issues: List[str]) -> tuple[Optional[str], Optional[str]]:
    issue_set = {str(issue) for issue in issues}
    if {"suspicious_artist", "suspicious_title"} & issue_set:
        return "Sanitize", "metadata-sanitation"
    if {"missing_artist", "missing_title", "weak_filename_parse"} & issue_set:
        return "Repair", "metadata-repair"
    return None, None


class TrackSummary(BaseModel):
    """Lightweight representation used in list/table responses."""

    id:           int
    filepath:     str
    filename:     str
    artist:       Optional[str] = None
    title:        Optional[str] = None
    genre:        Optional[str] = None
    bpm:          Optional[float] = None
    key_camelot:  Optional[str] = None
    key_musical:  Optional[str] = None
    duration_sec: Optional[float] = None
    bitrate_kbps: Optional[int] = None
    status:       str
    quality_tier: Optional[str] = None
    parse_confidence: Optional[str] = None
    issues:       List[str] = []
    recommended_action: Optional[str] = None
    recommended_route: Optional[str] = None

    @classmethod
    def from_track(cls, t: Track) -> "TrackSummary":
        recommended_action, recommended_route = _recommended_issue_route(t.issues)
        return cls(
            id=t.id,
            filepath=t.filepath,
            filename=t.filename,
            artist=t.artist,
            title=t.title,
            genre=t.genre,
            bpm=t.bpm,
            key_camelot=t.key_camelot,
            key_musical=t.key_musical,
            duration_sec=t.duration_sec,
            bitrate_kbps=t.bitrate_kbps,
            status=t.status,
            quality_tier=t.quality_tier,
            parse_confidence=t.parse_confidence,
            issues=t.issues,
            recommended_action=recommended_action,
            recommended_route=recommended_route,
        )


class TrackDetail(BaseModel):
    """Full field set returned for a single track."""

    id:             int
    filepath:       str
    filename:       str
    artist:         Optional[str] = None
    title:          Optional[str] = None
    genre:          Optional[str] = None
    bpm:            Optional[float] = None
    key_camelot:    Optional[str] = None
    key_musical:    Optional[str] = None
    duration_sec:   Optional[float] = None
    bitrate_kbps:   Optional[int] = None
    filesize_bytes: Optional[int] = None
    filesystem_path: str
    status:         str
    error_msg:      Optional[str] = None
    processed_at:   Optional[str] = None
    pipeline_ver:   Optional[str] = None
    quality_tier:   Optional[str] = None
    parse_confidence: Optional[str] = None
    enrichment_queue_item: Optional[Dict[str, Any]] = None
    issues:         List[str] = []
    recommended_action: Optional[str] = None
    recommended_route: Optional[str] = None

    @classmethod
    def from_track(
        cls,
        t: Track,
        *,
        enrichment_queue_item: Optional[Dict[str, Any]] = None,
    ) -> "TrackDetail":
        recommended_action, recommended_route = _recommended_issue_route(t.issues)
        return cls(
            id=t.id,
            filepath=t.filepath,
            filename=t.filename,
            artist=t.artist,
            title=t.title,
            genre=t.genre,
            bpm=t.bpm,
            key_camelot=t.key_camelot,
            key_musical=t.key_musical,
            duration_sec=t.duration_sec,
            bitrate_kbps=t.bitrate_kbps,
            filesize_bytes=t.filesize_bytes,
            filesystem_path=t.filepath,
            status=t.status,
            error_msg=t.error_msg,
            processed_at=t.processed_at,
            pipeline_ver=t.pipeline_ver,
            quality_tier=t.quality_tier,
            parse_confidence=t.parse_confidence,
            enrichment_queue_item=enrichment_queue_item,
            issues=t.issues,
            recommended_action=recommended_action,
            recommended_route=recommended_route,
        )


class TrackStats(BaseModel):
    """Aggregate counts returned by GET /api/tracks/stats."""

    total:          int
    by_status:      Dict[str, int]
    by_quality:     Dict[str, int]
    missing_bpm:    int
    missing_key:    int
    missing_artist: int
    missing_title:  int


class TrackIssueItem(BaseModel):
    """One entry in the issues list."""

    id:      int
    filepath: str
    filename: str
    artist:  Optional[str] = None
    title:   Optional[str] = None
    status:  str
    issues:  List[str]


class CompatibleTrackItem(BaseModel):
    """One ranked candidate returned by GET /api/tracks/{id}/compatible."""

    id:                   int
    filepath:             str
    filename:             str
    artist:               Optional[str] = None
    title:                Optional[str] = None
    display_title:        str
    genre:                Optional[str] = None
    bpm:                  Optional[float] = None
    key_camelot:          Optional[str] = None
    key_musical:          Optional[str] = None
    quality_tier:         Optional[str] = None
    parse_confidence:     Optional[str] = None
    status:               str
    issues:               List[str] = []
    match_type:           str
    compatibility_score:  float
    compatibility_reason: str
    bpm_delta:            Optional[float] = None

    @classmethod
    def from_track(
        cls,
        t: Track,
        *,
        match_type: str,
        compatibility_score: float,
        compatibility_reason: str,
        bpm_delta: Optional[float],
    ) -> "CompatibleTrackItem":
        return cls(
            id=t.id,
            filepath=t.filepath,
            filename=t.filename,
            artist=t.artist,
            title=t.title,
            display_title=(t.title or "").strip() or t.filename,
            genre=t.genre,
            bpm=t.bpm,
            key_camelot=t.key_camelot,
            key_musical=t.key_musical,
            quality_tier=t.quality_tier,
            parse_confidence=t.parse_confidence,
            status=t.status,
            issues=t.issues,
            match_type=match_type,
            compatibility_score=compatibility_score,
            compatibility_reason=compatibility_reason,
            bpm_delta=bpm_delta,
        )


class CompatibleTracksResponse(BaseModel):
    """Response for GET /api/tracks/{id}/compatible."""

    track_id:   int
    status:     str
    reason:     Optional[str] = None
    items:      List[CompatibleTrackItem] = []
