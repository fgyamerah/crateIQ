"""
Reconciliation ledger, findings, and plan routes.

  GET  /api/reconciliation/ledger                — list recent ledger entries
  GET  /api/reconciliation/ledger/{ledger_id}    — get one ledger entry
  POST /api/reconciliation/validate-plan         — validate a path-reconcile plan
  GET  /api/reconciliation/findings              — read-only orphan/stale-path findings
  GET  /api/reconciliation/reference-findings    — read-only secondary reference findings
  GET  /api/reconciliation/quarantine            — read-only quarantine listing
  POST /api/reconciliation/plans/propose         — propose (never apply) a reconciliation plan
  POST /api/reconciliation/reference-apply/preview — read-only reference-artifact revalidation
  POST /api/reconciliation/reference-apply         — confirmed single reference filepath or track-ID update
  GET  /api/reconciliation/reference-ledger        — list reference-artifact ledger entries
  GET  /api/reconciliation/reference-ledger/{id}   — get one reference-artifact ledger entry
  POST /api/reconciliation/reference-ledger/{id}/rollback — verified reference rollback
  POST /api/reconciliation/apply/preview         — DB-only apply eligibility check for reviewed actions
  POST /api/reconciliation/apply                 — confirmed DB-only apply of one reviewed action
  POST /api/reconciliation/ledger/{ledger_id}/rollback — confirmed DB-only rollback of an applied action
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, Query

from ...schemas.reconciliation import (
    ReconciliationLedgerEntry,
    ReconciliationApplyPreviewRequest,
    ReconciliationApplyRequest,
    ReconciliationRollbackRequest,
    ReconciliationPlanValidateRequest,
    ReconciliationPlanValidateResponse,
)
from ...schemas.reconciliation_findings import QuarantineListingResponse, ReconciliationFindingsResponse, ReferenceFindingsResponse
from ...schemas.reconciliation_plan import ReconciliationPlanProposeResponse
from ...schemas.reference_plan import (
    ReferenceApplyPreviewRequest,
    ReferenceApplyPreviewResponse,
    ReferenceApplyRequest,
    ReferenceRollbackRequest,
    ReferenceLedgerEntry,
    ReferencePlanProposeResponse,
    ReferencePlanValidateRequest,
    ReferencePlanValidateResponse,
)
from ...services import read_only as read_only_service
from ...services import reconciliation_findings_service
from ...services import reference_findings_service
from ...services import reconciliation_plan_service
from ...services import reconciliation_apply_service
from ...services import reference_plan_service
from ...services import reference_apply_service
from utils import path_reconciliation

router = APIRouter(tags=["reconciliation"])


@router.get("/reconciliation/ledger", response_model=List[ReconciliationLedgerEntry])
async def list_reconciliation_ledger(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> List[ReconciliationLedgerEntry]:
    rows = read_only_service.list_reconciliation_ledger(limit=limit, offset=offset)
    return [ReconciliationLedgerEntry(**row) for row in rows]


@router.get("/reconciliation/ledger/{ledger_id}", response_model=ReconciliationLedgerEntry)
async def get_reconciliation_ledger(ledger_id: str) -> ReconciliationLedgerEntry:
    row = read_only_service.get_reconciliation_ledger(ledger_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Reconciliation ledger entry {ledger_id!r} not found.",
        )
    return ReconciliationLedgerEntry(**row)


@router.post("/reconciliation/validate-plan", response_model=ReconciliationPlanValidateResponse)
async def validate_reconciliation_plan(
    body: ReconciliationPlanValidateRequest,
) -> ReconciliationPlanValidateResponse:
    if not body.plan_path and not body.latest:
        raise HTTPException(status_code=422, detail="provide plan_path or set latest=true")

    if body.plan_path:
        plan_path = Path(body.plan_path).expanduser().resolve()
    else:
        root = read_only_service.get_library_root()
        plan_path = path_reconciliation.path_reconcile_latest_plan_path(root)
        if plan_path is None:
            raise HTTPException(status_code=404, detail="no reconciliation plan json found")

    if not plan_path.exists():
        raise HTTPException(status_code=404, detail=f"plan json not found: {plan_path}")

    try:
        result = path_reconciliation.path_reconcile_validate_plan(plan_path)
        result = reconciliation_plan_service.augment_with_cross_action_checks(result)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ReconciliationPlanValidateResponse(**result)


@router.post("/reconciliation/plans/propose", response_model=ReconciliationPlanProposeResponse)
async def propose_reconciliation_plan() -> ReconciliationPlanProposeResponse:
    try:
        return ReconciliationPlanProposeResponse(**reconciliation_plan_service.propose_plan())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/reconciliation/reference-plan/propose", response_model=ReferencePlanProposeResponse)
async def propose_reference_plan() -> ReferencePlanProposeResponse:
    try:
        return ReferencePlanProposeResponse(**reference_plan_service.propose_plan())
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/reconciliation/reference-plan/validate", response_model=ReferencePlanValidateResponse)
async def validate_reference_plan(body: ReferencePlanValidateRequest) -> ReferencePlanValidateResponse:
    if not body.plan_path and not body.latest:
        raise HTTPException(status_code=422, detail="provide plan_path or set latest=true")
    try:
        if body.plan_path:
            plan_path = body.plan_path
        else:
            plan = reference_plan_service.latest_plan_path(read_only_service.get_library_root())
            if plan is None:
                raise HTTPException(status_code=404, detail="no reference-artifact plan json found")
            plan_path = plan
        return ReferencePlanValidateResponse(**reference_plan_service.validate_plan(plan_path))
    except HTTPException:
        raise
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _apply_error(exc: reconciliation_apply_service.ReconciliationApplyError) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)})


def _reference_preview_error(exc: reference_apply_service.ReferenceApplyPreviewError) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)})


@router.post("/reconciliation/reference-apply/preview", response_model=ReferenceApplyPreviewResponse)
async def preview_reference_apply(body: ReferenceApplyPreviewRequest) -> ReferenceApplyPreviewResponse:
    try:
        return ReferenceApplyPreviewResponse(**reference_apply_service.preview(
            body.plan_path, body.plan_id, body.reviewed_action_ids,
        ))
    except reference_apply_service.ReferenceApplyPreviewError as exc:
        raise _reference_preview_error(exc)


@router.post("/reconciliation/reference-apply")
async def apply_reference_action(body: ReferenceApplyRequest) -> dict:
    try:
        return reference_apply_service.apply(
            body.plan_path, body.plan_id, body.plan_sha256, body.reviewed_action_ids,
            confirm=body.confirm,
        )
    except reference_apply_service.ReferenceApplyPreviewError as exc:
        raise _reference_preview_error(exc)


@router.get("/reconciliation/reference-ledger", response_model=List[ReferenceLedgerEntry])
async def list_reference_ledger(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> List[ReferenceLedgerEntry]:
    try:
        return [ReferenceLedgerEntry(**row) for row in reference_apply_service.list_ledger(limit=limit, offset=offset)]
    except reference_apply_service.ReferenceApplyPreviewError as exc:
        raise _reference_preview_error(exc)


@router.get("/reconciliation/reference-ledger/{ledger_id}", response_model=ReferenceLedgerEntry)
async def get_reference_ledger(ledger_id: str) -> ReferenceLedgerEntry:
    try:
        row = reference_apply_service.get_ledger(ledger_id)
    except reference_apply_service.ReferenceApplyPreviewError as exc:
        raise _reference_preview_error(exc)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Reference-artifact ledger entry {ledger_id!r} not found.")
    return ReferenceLedgerEntry(**row)


@router.post("/reconciliation/reference-ledger/{ledger_id}/rollback")
async def rollback_reference_ledger(ledger_id: str, body: ReferenceRollbackRequest) -> dict:
    try:
        return reference_apply_service.rollback(ledger_id, confirm=body.confirm)
    except reference_apply_service.ReferenceApplyPreviewError as exc:
        raise _reference_preview_error(exc)


@router.post("/reconciliation/apply/preview")
async def preview_reconciliation_apply(body: ReconciliationApplyPreviewRequest) -> dict:
    try:
        return reconciliation_apply_service.preview(body.plan_path, body.reviewed_action_ids)
    except reconciliation_apply_service.ReconciliationApplyError as exc:
        raise _apply_error(exc)


@router.post("/reconciliation/apply")
async def apply_reconciliation_plan(body: ReconciliationApplyRequest) -> dict:
    try:
        return reconciliation_apply_service.apply(body.plan_path, body.plan_id, body.reviewed_action_ids, confirm=body.confirm)
    except reconciliation_apply_service.ReconciliationApplyError as exc:
        raise _apply_error(exc)


@router.post("/reconciliation/ledger/{ledger_id}/rollback")
async def rollback_reconciliation_ledger(ledger_id: str, body: ReconciliationRollbackRequest) -> dict:
    try:
        return reconciliation_apply_service.rollback(ledger_id, confirm=body.confirm)
    except reconciliation_apply_service.ReconciliationApplyError as exc:
        raise _apply_error(exc)


@router.get("/reconciliation/findings", response_model=ReconciliationFindingsResponse)
async def get_reconciliation_findings() -> ReconciliationFindingsResponse:
    try:
        return ReconciliationFindingsResponse(**reconciliation_findings_service.get_findings())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/reconciliation/reference-findings", response_model=ReferenceFindingsResponse)
async def get_reference_findings() -> ReferenceFindingsResponse:
    try:
        return ReferenceFindingsResponse(**reference_findings_service.get_reference_findings())
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/reconciliation/quarantine", response_model=QuarantineListingResponse)
async def get_reconciliation_quarantine() -> QuarantineListingResponse:
    try:
        return QuarantineListingResponse(**reconciliation_findings_service.get_quarantine_listing())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
