import { apiFetch } from './client'
import type {
  QuarantineListingResponse,
  ReconciliationFindingsResponse,
  ReconciliationLedgerEntry,
  ReconciliationPlanProposeResponse,
  ReconciliationPlanValidateRequest,
  ReconciliationPlanValidationResult,
  ReconciliationApplyPreviewRequest,
  ReconciliationApplyPreviewResponse,
  ReconciliationApplyRequest,
  ReconciliationApplyResponse,
  ReconciliationRollbackResponse,
} from '../types/reconciliation'

export function fetchReconciliationLedger(limit = 50, offset = 0): Promise<ReconciliationLedgerEntry[]> {
  return apiFetch.get<ReconciliationLedgerEntry[]>(`/reconciliation/ledger?limit=${limit}&offset=${offset}`)
}

export function fetchReconciliationLedgerEntry(ledgerId: string): Promise<ReconciliationLedgerEntry> {
  return apiFetch.get<ReconciliationLedgerEntry>(`/reconciliation/ledger/${encodeURIComponent(ledgerId)}`)
}

export function validateReconciliationPlan(
  req: ReconciliationPlanValidateRequest,
): Promise<ReconciliationPlanValidationResult> {
  return apiFetch.post<ReconciliationPlanValidationResult>('/reconciliation/validate-plan', req)
}

export function fetchReconciliationFindings(): Promise<ReconciliationFindingsResponse> {
  return apiFetch.get<ReconciliationFindingsResponse>('/reconciliation/findings')
}

export function fetchReconciliationQuarantine(): Promise<QuarantineListingResponse> {
  return apiFetch.get<QuarantineListingResponse>('/reconciliation/quarantine')
}

export function proposeReconciliationPlan(): Promise<ReconciliationPlanProposeResponse> {
  return apiFetch.post<ReconciliationPlanProposeResponse>('/reconciliation/plans/propose', {})
}

export function previewReconciliationApply(req: ReconciliationApplyPreviewRequest): Promise<ReconciliationApplyPreviewResponse> {
  return apiFetch.post<ReconciliationApplyPreviewResponse>('/reconciliation/apply/preview', req)
}

export function applyReconciliationPlan(req: ReconciliationApplyRequest): Promise<ReconciliationApplyResponse> {
  return apiFetch.post<ReconciliationApplyResponse>('/reconciliation/apply', req)
}

export function rollbackReconciliationLedger(ledgerId: string, confirm: boolean): Promise<ReconciliationRollbackResponse> {
  return apiFetch.post<ReconciliationRollbackResponse>(`/reconciliation/ledger/${encodeURIComponent(ledgerId)}/rollback`, { confirm })
}
