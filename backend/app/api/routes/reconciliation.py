"""
Reconciliation ledger and findings routes.

  GET /api/reconciliation/ledger                — list recent ledger entries
  GET /api/reconciliation/ledger/{ledger_id}    — get one ledger entry
  POST /api/reconciliation/validate-plan        — validate a path-reconcile plan
  GET /api/reconciliation/findings              — read-only orphan/stale-path findings
  GET /api/reconciliation/quarantine            — read-only quarantine listing
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, Query

from ...schemas.reconciliation import (
    ReconciliationLedgerEntry,
    ReconciliationPlanValidateRequest,
    ReconciliationPlanValidateResponse,
)
from ...schemas.reconciliation_findings import QuarantineListingResponse, ReconciliationFindingsResponse
from ...services import read_only as read_only_service
from ...services import reconciliation_findings_service
import pipeline

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
        plan_path = pipeline._path_reconcile_latest_plan_path(root)
        if plan_path is None:
            raise HTTPException(status_code=404, detail="no reconciliation plan json found")

    if not plan_path.exists():
        raise HTTPException(status_code=404, detail=f"plan json not found: {plan_path}")

    try:
        result = pipeline._path_reconcile_validate_plan(plan_path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ReconciliationPlanValidateResponse(**result)


@router.get("/reconciliation/findings", response_model=ReconciliationFindingsResponse)
async def get_reconciliation_findings() -> ReconciliationFindingsResponse:
    try:
        return ReconciliationFindingsResponse(**reconciliation_findings_service.get_findings())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/reconciliation/quarantine", response_model=QuarantineListingResponse)
async def get_reconciliation_quarantine() -> QuarantineListingResponse:
    try:
        return QuarantineListingResponse(**reconciliation_findings_service.get_quarantine_listing())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
