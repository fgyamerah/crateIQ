"""
Publish routes.

  GET  /api/publish/readiness/{crate_id}     — read-only readiness contract
  GET  /api/publish/export/{crate_id}/preview — export preview (no side effects)
  POST /api/publish/export/{crate_id}         — confirmed, guarded export
  POST /api/publish/sync/preview              — sync dry-run preview
  POST /api/publish/sync/confirm              — confirmed, guarded sync (no --delete)
  GET  /api/publish/sync/{operation_id}       — live status; verifies once the job ends
  GET  /api/publish/operations                — recent publish operations
  GET  /api/publish/operations/{operation_id} — one operation's detail
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from ...schemas.export import CrateLineEndings, CratePathMode
from ...schemas.publish import (
    PublishExportPreview,
    PublishExportRequest,
    PublishExportResult,
    PublishExportTarget,
    PublishOperationSummary,
    PublishReadiness,
    PublishSyncConfirmRequest,
    PublishSyncConfirmResponse,
    PublishSyncPreview,
    PublishSyncPreviewRequest,
    PublishSyncSource,
    PublishSyncStatus,
)
from ...services import (
    publish_export_service,
    publish_operations_service,
    publish_readiness_service,
    publish_sync_service,
)
from ...services.publish_export_service import PublishExportBlocked
from ...services.publish_sync_service import PublishSyncBlocked

router = APIRouter(tags=["publish"])


@router.get("/publish/readiness/{crate_id}", response_model=PublishReadiness)
async def get_publish_readiness(
    crate_id: int,
    export_target: PublishExportTarget = "m3u8",
    sync_source: PublishSyncSource = "library",
) -> PublishReadiness:
    """
    Return a truthful readiness snapshot for publishing one Manual Crate.

    A ready state is not an approval — the guided export/sync flow still
    requires an explicit preview and explicit confirmation before anything
    is written.
    """
    result = publish_readiness_service.get_readiness(crate_id, export_target, sync_source)
    if result is None:
        raise HTTPException(status_code=404, detail="Crate not found")
    return result


@router.get("/publish/export/{crate_id}/preview", response_model=PublishExportPreview)
async def preview_publish_export(
    crate_id: int,
    export_target: PublishExportTarget = "m3u8",
    path_mode: CratePathMode = "filename",
    include_metadata: bool = True,
    line_endings: CrateLineEndings = "lf",
) -> PublishExportPreview:
    """
    Preview a guarded export without writing anything.

    Delegates to the existing per-format preview (crate/Rekordbox/Serato)
    and adds the exact would-be output path so the UI can show it before
    the user confirms.
    """
    result = publish_export_service.preview(crate_id, export_target, path_mode, include_metadata, line_endings)
    if result is None:
        raise HTTPException(status_code=404, detail="Crate not found")
    return result


@router.post("/publish/export/{crate_id}", response_model=PublishExportResult)
async def confirm_publish_export(crate_id: int, body: PublishExportRequest) -> PublishExportResult:
    """
    Execute a guarded export. Requires body.confirm == true.

    Persists the confirmed operation to the publish operations history and
    verifies the artifact after writing; verification failure is reported
    separately from execution failure.
    """
    try:
        result = publish_export_service.execute(
            crate_id,
            body.export_target,
            body.path_mode,
            body.include_metadata,
            body.line_endings,
            confirm=body.confirm,
        )
    except PublishExportBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="Crate not found")
    return result


@router.post("/publish/sync/preview", response_model=PublishSyncPreview)
async def preview_publish_sync(body: PublishSyncPreviewRequest) -> PublishSyncPreview:
    """
    Dry-run sync preview. Blocked destinations never spawn rsync; otherwise
    delegates to the existing safe rsync --dry-run preview unchanged.
    """
    return await publish_sync_service.preview(body.sync_source)


@router.post("/publish/sync/confirm", response_model=PublishSyncConfirmResponse, status_code=202)
async def confirm_publish_sync(body: PublishSyncConfirmRequest) -> PublishSyncConfirmResponse:
    """
    Execute a guarded sync. Requires body.confirm == true. Never passes
    --delete to rsync. Returns immediately with an operation_id and job_id;
    poll GET /api/publish/sync/{operation_id} for live status/verification.
    """
    try:
        return publish_sync_service.confirm(body.sync_source, body.confirm)
    except PublishSyncBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/publish/sync/{operation_id}", response_model=PublishSyncStatus)
async def get_publish_sync_status(operation_id: str) -> PublishSyncStatus:
    """
    Return live sync status. Once the underlying rsync job reaches a
    terminal state, this call lazily runs verification (a second safe
    dry-run preview) exactly once and persists the result.
    """
    result = await publish_sync_service.get_status(operation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Publish sync operation not found")
    return result


@router.get("/publish/operations", response_model=List[PublishOperationSummary])
async def list_publish_operations(
    limit: int = Query(default=50, ge=1, le=200),
    operation_type: Optional[str] = Query(default=None, pattern="^(export|sync)$"),
) -> List[PublishOperationSummary]:
    return [PublishOperationSummary(**row) for row in publish_operations_service.list_recent(limit, operation_type)]


@router.get("/publish/operations/{operation_id}", response_model=PublishOperationSummary)
async def get_publish_operation(operation_id: str) -> PublishOperationSummary:
    row = publish_operations_service.get_operation(operation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Publish operation not found")
    return PublishOperationSummary(**row)
