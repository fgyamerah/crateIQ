"""
Playlists routes.

  POST  /api/playlists/set-builder          — dispatch a set-builder job
  GET   /api/playlists                      — list saved set playlists (from pipeline DB)
  GET   /api/playlists/{playlist_id}        — get a playlist with its track list
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Query

from ...schemas.job import JobResponse
from ...schemas.playlist import (
    PlaylistDetail,
    PlaylistSummary,
    SetBuilderJobResponse,
    SetBuilderRequest,
)
from ...services import job_service, toolkit_runner
from ...services import playlist_service

log = logging.getLogger(__name__)
router = APIRouter(tags=["playlists"])


# ---------------------------------------------------------------------------
# POST /api/playlists/set-builder
# ---------------------------------------------------------------------------

@router.post("/playlists/set-builder", response_model=SetBuilderJobResponse, status_code=202)
async def run_set_builder(body: SetBuilderRequest) -> SetBuilderJobResponse:
    """
    Dispatch a set-builder pipeline job.

    Builds an energy-curve DJ set from the library database.
    Returns a job_id immediately; poll GET /api/jobs/{job_id} for status.
    When the job succeeds the playlist is saved to the pipeline DB and will
    appear in GET /api/playlists.
    """
    args: list[str] = []

    args += ["--vibe", body.vibe]
    args += ["--duration", str(body.duration)]
    args += ["--strategy", body.strategy]
    args += ["--structure", body.structure]

    if body.genre:
        args += ["--genre", body.genre]

    args += ["--max-bpm-jump", str(body.max_bpm_jump)]
    args += ["--artist-repeat-window", str(body.artist_repeat_window)]

    if not body.strict_harmonic:
        args.append("--no-strict-harmonic")

    if body.name:
        args += ["--name", body.name]

    if body.dry_run:
        args.append("--dry-run")

    # Validate through the allowlist before touching the DB
    try:
        toolkit_runner.build_command("set-builder", args)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    job = job_service.create_job("set-builder", args)

    try:
        toolkit_runner.create_and_start_job(
            job.id, job.command, job.args, library_key=job.library_key
        )
    except Exception as exc:
        job_service.mark_finished(
            job.id, status="failed", exit_code=-1, library_key=job.library_key
        )
        log.exception("Failed to start set-builder job %s: %s", job.id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to start job: {exc}")

    return SetBuilderJobResponse(
        job_id=job.id,
        message=f"Set-builder job queued. vibe={body.vibe} duration={body.duration}min strategy={body.strategy}",
    )


# ---------------------------------------------------------------------------
# GET /api/playlists
# ---------------------------------------------------------------------------

@router.get("/playlists", response_model=List[PlaylistSummary])
async def list_playlists(
    limit:  int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0,  ge=0),
) -> List[PlaylistSummary]:
    """
    Return saved set playlists from the pipeline DB, newest first.

    Returns an empty list if no pipeline run has been performed yet.
    """
    return playlist_service.list_playlists(limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /api/playlists/{playlist_id}
# ---------------------------------------------------------------------------

@router.get("/playlists/{playlist_id}", response_model=PlaylistDetail)
async def get_playlist(playlist_id: int) -> PlaylistDetail:
    """
    Return a single playlist with its full ordered track list.

    Track metadata (artist, title, BPM, key, genre) is joined from the
    pipeline tracks table.
    """
    detail = playlist_service.get_playlist_detail(playlist_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Playlist {playlist_id} not found")
    return detail
