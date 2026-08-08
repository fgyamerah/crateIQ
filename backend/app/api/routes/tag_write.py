"""
Controlled metadata write-back routes (Cycle 7).

POST /api/tag-write/plan                                  — read-only exact write plan
POST /api/tag-write/apply                                 — backup, write, verify (confirm required)
GET  /api/tag-write/operations                             — recent operation history
GET  /api/tag-write/operations/{operation_id}               — one operation's full detail
POST /api/tag-write/operations/{operation_id}/restore/{track_id} — restore one file from backup
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query

from ...schemas.tag_write import (
    TagWriteApplyRequest, TagWriteApplyResult, TagWriteOperation,
    TagWritePlanRequest, TagWritePlanResponse, TagWriteRestoreRequest, TagWriteRestoreResult,
)
from ...services import tag_write_service as service

router = APIRouter(tags=["tag-write"])


@router.post("/tag-write/plan", response_model=TagWritePlanResponse)
async def plan(body: TagWritePlanRequest) -> TagWritePlanResponse:
    try:
        return TagWritePlanResponse(**service.build_plan(body.track_ids))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/tag-write/apply", response_model=TagWriteApplyResult)
async def apply(body: TagWriteApplyRequest) -> TagWriteApplyResult:
    expected = {track_id: item.model_dump() for track_id, item in body.expected.items()}
    try:
        return TagWriteApplyResult(**service.apply_plan(body.track_ids, expected, confirm=body.confirm))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/tag-write/operations", response_model=list[TagWriteOperation])
async def list_operations(limit: int = Query(default=20, ge=1, le=100)) -> list[TagWriteOperation]:
    return [TagWriteOperation(**op) for op in service.list_operations(limit)]


@router.get("/tag-write/operations/{operation_id}", response_model=TagWriteOperation)
async def get_operation(operation_id: str) -> TagWriteOperation:
    operation = service.get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Write-back operation was not found.")
    return TagWriteOperation(**operation)


@router.post("/tag-write/operations/{operation_id}/restore/{track_id}", response_model=TagWriteRestoreResult)
async def restore(
    body: TagWriteRestoreRequest,
    operation_id: str = Path(...),
    track_id: int = Path(ge=1),
) -> TagWriteRestoreResult:
    if body.track_id != track_id:
        raise HTTPException(status_code=422, detail="track_id in the path and body must match.")
    try:
        return TagWriteRestoreResult(**service.restore_file(operation_id, track_id, confirm=body.confirm))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
