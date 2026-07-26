"""
Track routes — read-only views into the pipeline's processed.db.

  GET /api/tracks          — list tracks with filtering, search, sort, pagination
  GET /api/tracks/stats    — aggregate counts (status, quality, missing fields)
  GET /api/tracks/issues   — grouped issue counts
  GET /api/tracks/{id}     — single track detail

IMPORTANT: /stats and /issues must be registered before /{id} so FastAPI
does not interpret the literal strings as integer IDs.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...schemas.track import CompatibleTracksResponse, TrackDetail, TrackStats, TrackSummary
from ...services import read_only as read_only_service
from ...services import track_service

log = logging.getLogger(__name__)
router = APIRouter(tags=["tracks"])
MAX_TRACK_LIMIT = 500


class TrackPageResponse(BaseModel):
    items: List[TrackSummary]
    limit: int
    offset: int
    total: int


class TrackIssueCountsResponse(BaseModel):
    missing_artist: int
    missing_title: int
    weak_filename_parse: int
    suspicious_artist: int
    suspicious_title: int


# ---------------------------------------------------------------------------
# GET /api/tracks/stats
# ---------------------------------------------------------------------------

@router.get("/tracks/stats", response_model=TrackStats)
async def get_track_stats() -> TrackStats:
    """
    Return aggregate counts for the whole library:
    - total tracks and breakdown by status
    - breakdown by quality tier
    - counts of tracks missing BPM, key, artist, or title
    """
    return track_service.get_stats()


# ---------------------------------------------------------------------------
# GET /api/tracks/issues
# ---------------------------------------------------------------------------

@router.get("/tracks/issues", response_model=TrackIssueCountsResponse)
async def get_track_issues() -> TrackIssueCountsResponse:
    """Return grouped issue counts across the selected library root."""
    return TrackIssueCountsResponse(**track_service.get_issue_counts())


# ---------------------------------------------------------------------------
# GET /api/tracks
# ---------------------------------------------------------------------------

@router.get("/tracks", response_model=TrackPageResponse)
async def list_tracks(
    search: Optional[str] = Query(default=None, description="Search artist, title, filename"),
    artist: Optional[str] = Query(default=None, description="Exact artist match (case-insensitive)"),
    status: Optional[str] = Query(default=None, description="Filter by status"),
    issue: Optional[str] = Query(default=None, description="Filter by issue flag"),
    bpm_min: Optional[float] = Query(default=None, ge=0),
    bpm_max: Optional[float] = Query(default=None, ge=0),
    has_key: Optional[bool] = Query(default=None, description="Filter tracks with or without a key"),
    genre: Optional[str] = Query(default=None, description="Case-insensitive genre filter"),
    parse_confidence: Optional[str] = Query(default=None, description="Filter by filename parse confidence"),
    sort: str = Query(default="artist", description="Sort key"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=MAX_TRACK_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> TrackPageResponse:
    """List library tracks with read-only filtering and pagination."""
    tracks, _total = track_service.list_tracks(
        q=search,
        status=status,
        artist=artist,
        issue=issue,
        bpm_min=bpm_min,
        bpm_max=bpm_max,
        has_key=has_key,
        genre=genre,
        parse_confidence=parse_confidence,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    return TrackPageResponse(
        items=[TrackSummary.from_track(t) for t in tracks],
        limit=limit,
        offset=offset,
        total=_total,
    )


# ---------------------------------------------------------------------------
# GET /api/tracks/{track_id}
# ---------------------------------------------------------------------------

@router.get("/tracks/{track_id}", response_model=TrackDetail)
async def get_track(track_id: int) -> TrackDetail:
    """Return the full detail record for a single track."""
    track = track_service.get_track_by_id(track_id)
    if not track:
        raise HTTPException(status_code=404, detail=f"Track {track_id} not found.")
    queue_item = read_only_service.lookup_enrichment_queue_item(track.filepath)
    return TrackDetail.from_track(track, enrichment_queue_item=queue_item)


# ---------------------------------------------------------------------------
# GET /api/tracks/{track_id}/compatible
# ---------------------------------------------------------------------------

@router.get("/tracks/{track_id}/compatible", response_model=CompatibleTracksResponse)
async def get_compatible_tracks(
    track_id: int,
    limit: int = Query(default=8, ge=1, le=25, description="Max compatible tracks to return"),
    bpm_tolerance: float = Query(default=6.0, ge=1.0, le=60.0, description="Max BPM delta to include"),
    include_same_key: bool = Query(default=True, description="Include exact Camelot key matches"),
    include_adjacent: bool = Query(default=True, description="Include ±1 Camelot wheel position matches"),
    genre: Optional[str] = Query(default=None, description="Restrict candidates to this genre (case-insensitive)"),
) -> CompatibleTracksResponse:
    """
    Return harmonically compatible tracks for a single track, read-only.

    Compatibility is limited to the three standard Camelot mixing relations
    (same key, adjacent wheel position, relative major/minor) — relative-key
    matches are always included; include_same_key/include_adjacent gate the
    other two. BPM and genre only affect ranking/inclusion tolerance, never
    trigger audio scanning or writes. Tracks without a Camelot key return a
    "missing_key" status with an empty list rather than a guess. A genuine
    query/database failure raises HTTP 500 with a generic message rather
    than silently reporting "ok, no matches".
    """
    try:
        result = track_service.get_compatible_tracks(
            track_id,
            limit=limit,
            bpm_tolerance=bpm_tolerance,
            include_same_key=include_same_key,
            include_adjacent=include_adjacent,
            genre=genre,
        )
    except track_service.CompatibleTracksQueryError:
        raise HTTPException(
            status_code=500,
            detail="Compatible-tracks lookup failed. Check server logs for details.",
        )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Track {track_id} not found.")
    return result
