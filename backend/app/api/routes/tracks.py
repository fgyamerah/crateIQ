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
import mimetypes
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...schemas.track import CompatibleTracksResponse, TrackDetail, TrackStats, TrackSummary
from ...services import read_only as read_only_service
from ...services import track_service
from ...core.library_root import assert_path_under_root, selected_library_root

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


def _preview_audio_path(track_id: int) -> Path:
    """Resolve one DB-backed audio path without allowing arbitrary file reads."""
    track = track_service.get_track_by_id(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    if not track.filepath:
        raise HTTPException(status_code=404, detail="Preview audio is unavailable for this track")
    try:
        path = assert_path_under_root(Path(track.filepath), selected_library_root())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Preview audio path is outside the selected library") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Preview audio file is unavailable")
    return path


def _parse_byte_range(header: str, size: int) -> tuple[int, int]:
    """Parse one RFC 7233 byte range; multi-range responses are unnecessary here."""
    if not header.startswith("bytes=") or "," in header:
        raise ValueError("invalid range")
    start_text, _, end_text = header[6:].partition("-")
    if not _:
        raise ValueError("invalid range")
    if not start_text:
        if not end_text:
            raise ValueError("invalid range")
        length = int(end_text)
        if length <= 0:
            raise ValueError("invalid range")
        return max(0, size - length), size - 1
    start = int(start_text)
    end = int(end_text) if end_text else size - 1
    if start < 0 or end < start or start >= size:
        raise ValueError("invalid range")
    return start, min(end, size - 1)


def _file_bytes(path: Path, start: int, end: int, chunk_size: int = 64 * 1024):
    with path.open("rb") as stream:
        stream.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk = stream.read(min(chunk_size, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk


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
# GET /api/tracks/{track_id}/preview-audio
# ---------------------------------------------------------------------------

@router.get("/tracks/{track_id}/preview-audio")
async def preview_audio(track_id: int, request: Request):
    """Stream an allowed local audio file for native browser preview only."""
    path = _preview_audio_path(track_id)
    size = path.stat().st_size
    if size <= 0:
        raise HTTPException(status_code=404, detail="Preview audio file is unavailable")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {"Accept-Ranges": "bytes"}
    range_header = request.headers.get("range")
    if not range_header:
        headers["Content-Length"] = str(size)
        return StreamingResponse(_file_bytes(path, 0, max(0, size - 1)), media_type=media_type, headers=headers)
    try:
        start, end = _parse_byte_range(range_header, size)
    except (TypeError, ValueError):
        raise HTTPException(status_code=416, detail="Requested preview range is invalid", headers={"Content-Range": f"bytes */{size}"})
    headers.update({"Content-Length": str(end - start + 1), "Content-Range": f"bytes {start}-{end}/{size}"})
    return StreamingResponse(_file_bytes(path, start, end), status_code=206, media_type=media_type, headers=headers)


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
