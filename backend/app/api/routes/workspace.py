"""
Managed music workspace API (Cycle 9).

  GET  /api/workspace/status              — read-only state: managed_workspace | legacy_direct_library | not_configured
  POST /api/workspace/configure            — idempotently create Inbox/Library/Quarantine + marker
  POST /api/workspace/import               — copy external files/folders into Inbox
  GET  /api/workspace/inbox/tracks         — list Inbox-zone tracks
  GET  /api/workspace/promotion/preview    — read-only promotion readiness for Inbox tracks
  POST /api/workspace/promotion/apply      — explicit "Move Ready to Library"
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...core.library_root import selected_library_root
from ...schemas.track import TrackSummary
from ...services import track_service, workspace_service

router = APIRouter(tags=["workspace"])


class WorkspaceStatusResponse(BaseModel):
    state: str
    library_root: str
    inbox_path: Optional[str] = None
    library_path: Optional[str] = None
    quarantine_path: Optional[str] = None
    marker_version: Optional[int] = None
    message: str


class WorkspaceImportRequest(BaseModel):
    source_paths: List[str] = Field(min_length=1, max_length=200)
    confirm: bool = False


class TrackPageResponse(BaseModel):
    items: List[TrackSummary]
    limit: int
    offset: int
    total: int


class PromotionPreviewRequest(BaseModel):
    track_ids: Optional[List[int]] = None


class PromotionApplyRequest(BaseModel):
    track_ids: List[int] = Field(min_length=1, max_length=200)
    confirm: bool = False


def _root():
    try:
        return selected_library_root()
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/workspace/status", response_model=WorkspaceStatusResponse)
async def get_workspace_status() -> WorkspaceStatusResponse:
    return WorkspaceStatusResponse(**workspace_service.workspace_state(_root()))


@router.post("/workspace/configure", response_model=WorkspaceStatusResponse)
async def configure_workspace() -> WorkspaceStatusResponse:
    try:
        return WorkspaceStatusResponse(**workspace_service.configure_workspace(_root()))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/workspace/import")
async def import_to_inbox(body: WorkspaceImportRequest):
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Import requires confirm=true.")
    try:
        return workspace_service.import_sources(_root(), body.source_paths, confirm=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/workspace/inbox/tracks", response_model=TrackPageResponse)
async def list_inbox_tracks(
    search: Optional[str] = Query(default=None, description="Search artist, title, filename"),
    sort: str = Query(default="artist", description="Sort key"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> TrackPageResponse:
    tracks, total = track_service.list_tracks(
        q=search, storage_zone="INBOX", sort=sort, order=order, limit=limit, offset=offset,
    )
    return TrackPageResponse(
        items=[TrackSummary.from_track(t) for t in tracks], limit=limit, offset=offset, total=total,
    )


@router.post("/workspace/promotion/preview")
async def preview_promotion(body: PromotionPreviewRequest):
    try:
        return workspace_service.promotion_preview(_root(), body.track_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/workspace/promotion/apply")
async def apply_promotion(body: PromotionApplyRequest):
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Promotion requires confirm=true after reviewing the preview.")
    try:
        return workspace_service.promote_tracks(_root(), body.track_ids, confirm=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
