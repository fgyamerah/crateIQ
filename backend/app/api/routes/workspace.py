"""
Managed music workspace API (Cycle 9).

  GET  /api/workspace/status              — read-only state: managed_workspace | legacy_direct_library | not_configured
  POST /api/workspace/configure            — idempotently create Inbox/Library/Quarantine + marker
  POST /api/workspace/import               — copy external files/folders into Inbox
  GET  /api/workspace/inbox/tracks         — list Inbox-zone tracks
  GET  /api/workspace/promotion/preview    — read-only promotion readiness for Inbox tracks
  POST /api/workspace/promotion/apply      — explicit "Move Ready to Library"

Batch preparation (Cycle 10):
  GET  /api/workspace/prepare/preview          — read-only Process All preflight
  POST /api/workspace/prepare/start            — explicit, confirmed Process All (async, cancellable)
  POST /api/workspace/prepare/clean            — Clean Selected (synchronous, deterministic)
  POST /api/workspace/prepare/enrich           — Enrich Selected (synchronous, bounded network)
  GET  /api/workspace/prepare/operations/{id}  — poll an operation's progress/result
  GET  /api/workspace/prepare/operations       — recent operation history
  POST /api/workspace/prepare/operations/{id}/cancel — request cancellation

Multi-provider enrichment (Cycle 11):
  POST /api/workspace/enrichment/consensus/{track_id} — gathers multi-
       provider evidence (real bounded network calls for configured
       providers) and returns a field-by-field consensus verdict. No
       track metadata is changed; POST because it is explicit and
       network-triggering, matching the existing online-lookup action.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...core.library_root import selected_library_root
from ...schemas.track import TrackSummary
from ...services import (
    preparation_operations_service,
    preparation_service,
    provider_routing_service,
    track_service,
    workspace_service,
)

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


class WorkspaceRootClassifyRequest(BaseModel):
    library_root: str = Field(min_length=1, max_length=4096)


class WorkspaceRootCreateRequest(WorkspaceRootClassifyRequest):
    confirm: bool = False


class WorkspaceRootClassifyResponse(BaseModel):
    state: str
    library_root: str
    inbox_path: Optional[str] = None
    library_path: Optional[str] = None
    quarantine_path: Optional[str] = None
    marker_version: Optional[int] = None
    exists: bool
    parent_exists: bool
    parent_writable: Optional[bool] = None
    can_create: bool
    message: str


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


class ProcessAllRequest(BaseModel):
    confirm: bool = False


class TrackIdsRequest(BaseModel):
    track_ids: List[int] = Field(min_length=1, max_length=200)


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


@router.post("/workspace/root/classify", response_model=WorkspaceRootClassifyResponse)
async def classify_workspace_root(body: WorkspaceRootClassifyRequest) -> WorkspaceRootClassifyResponse:
    """Read-only: classify a candidate workspace root path. Never touches disk."""
    try:
        return WorkspaceRootClassifyResponse(**workspace_service.classify_root_candidate(body.library_root))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/workspace/root/create", response_model=WorkspaceRootClassifyResponse)
async def create_workspace_root(body: WorkspaceRootCreateRequest) -> WorkspaceRootClassifyResponse:
    """Safely create only the final requested directory for a new workspace root."""
    try:
        return WorkspaceRootClassifyResponse(**workspace_service.create_root_directory(body.library_root, confirm=body.confirm))
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


# ---------------------------------------------------------------------------
# Batch preparation (Cycle 10)
# ---------------------------------------------------------------------------

@router.get("/workspace/prepare/preview")
async def preview_prepare():
    """Read-only Process All preflight. Never starts processing."""
    try:
        return preparation_service.preflight_preview(_root())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/workspace/prepare/start")
async def start_prepare(body: ProcessAllRequest):
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Process All requires confirm=true after reviewing the preflight preview.")
    try:
        return preparation_service.start_process_all(_root(), confirm=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/workspace/prepare/clean")
async def clean_selected(body: TrackIdsRequest):
    try:
        return preparation_service.clean_tracks(_root(), body.track_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/workspace/prepare/enrich")
async def enrich_selected(body: TrackIdsRequest):
    try:
        return preparation_service.enrich_tracks(_root(), body.track_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/workspace/prepare/operations")
async def list_prepare_operations(limit: int = Query(default=20, ge=1, le=100)):
    return preparation_operations_service.list_recent(limit)


@router.get("/workspace/prepare/operations/{operation_id}")
async def get_prepare_operation(operation_id: str):
    operation = preparation_operations_service.get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Operation not found.")
    return operation


@router.post("/workspace/prepare/operations/{operation_id}/cancel")
async def cancel_prepare_operation(operation_id: str):
    operation = preparation_operations_service.request_cancel(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Operation not found.")
    return operation


# ---------------------------------------------------------------------------
# Multi-provider consensus preview (Cycle 11)
# ---------------------------------------------------------------------------

@router.post("/workspace/enrichment/consensus/{track_id}")
async def preview_track_consensus(track_id: int):
    """
    Gathers evidence from every currently-configured provider for one
    track and returns the full explainable field-by-field HIGH/MEDIUM/
    LOW/CONFLICT verdict. Makes no metadata/tag changes to the track
    itself -- but, like every other "online lookup" action in this app,
    a stage of this gathering (Beets/MusicBrainz, and AcoustID/Discogs/
    etc. once configured) makes real bounded network calls and persists
    them to the existing enrichment_review_service decision queue, the
    same shared bookkeeping the Enrichment Review page already writes to.
    This is why it is a POST, explicit and user-triggered, not a GET.
    """
    try:
        return provider_routing_service.preview_consensus(_root(), track_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
