"""
Pydantic schemas for reconciliation ledger read-only endpoints.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ReconciliationLedgerEntry(BaseModel):
    ledger_id: str
    created_at: Optional[str] = None
    root: Optional[str] = None
    operation_type: Optional[str] = None
    old_path: Optional[str] = None
    new_path: Optional[str] = None
    affected_tables: Optional[str] = None
    before_values_json: Optional[str] = None
    after_values_json: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None


class ReconciliationPlanValidateRequest(BaseModel):
    plan_path: Optional[str] = None
    latest: bool = False


class ReconciliationPlanValidateResponse(BaseModel):
    generated_at: str
    plan_path: str
    root: str
    total_actions: int
    valid_actions: int
    invalid_actions: int
    skipped_actions: int
    reasons: Dict[str, int]
    validation_records: List[Dict[str, Any]]


class ReconciliationApplyPreviewRequest(BaseModel):
    plan_path: str
    reviewed_action_ids: List[str]


class ReconciliationApplyRequest(ReconciliationApplyPreviewRequest):
    plan_id: str
    confirm: bool = False


class ReconciliationRollbackRequest(BaseModel):
    confirm: bool = False
