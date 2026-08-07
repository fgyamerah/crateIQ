export interface ReconciliationLedgerEntry {
  ledger_id: string
  created_at: string | null
  root: string | null
  operation_type: string | null
  old_path: string | null
  new_path: string | null
  affected_tables: string | null
  before_values_json: string | null
  after_values_json: string | null
  status: string | null
  error: string | null
}

export interface ReconciliationPlanValidationRecord {
  action: Record<string, unknown> | unknown
  action_type: string
  status: 'valid' | 'invalid' | 'skipped'
  reason: string | null
  issues: string[]
  warnings: string[]
}

export interface ReconciliationPlanValidationResult {
  generated_at: string
  plan_path: string
  root: string
  total_actions: number
  valid_actions: number
  invalid_actions: number
  skipped_actions: number
  reasons: Record<string, number>
  validation_records: ReconciliationPlanValidationRecord[]
}

export interface ReconciliationPlanValidateRequest {
  plan_path?: string | null
  latest?: boolean
}

export type FindingType = 'indexed_missing_file' | 'untracked_file' | 'stale_path' | 'path_candidate'

export interface FindingPathSide {
  relative_path: string | null
  filename: string | null
  track_id: number | null
  status: string | null
  stage: string | null
  size_bytes: number | null
}

export interface ReconciliationFinding {
  finding_id: string
  finding_type: FindingType
  summary: string
  db_side: FindingPathSide | null
  filesystem_side: FindingPathSide | null
  evidence: Record<string, unknown>
}

export interface ReconciliationFindingsSummary {
  indexed_missing_file: number
  untracked_file: number
  stale_path: number
  path_candidate: number
}

export interface ReconciliationFindingsResponse {
  summary: ReconciliationFindingsSummary
  findings: ReconciliationFinding[]
  warnings: string[]
  generated_at: string | null
  message: string | null
}

export interface QuarantineItem {
  relative_path: string
  filename: string
  size_bytes: number | null
  original_relative_path: string | null
  reason: string | null
  operation_id: string | null
  quarantined_at: string | null
  restore_supported: boolean
}

export interface QuarantineListingResponse {
  supported: boolean
  items: QuarantineItem[]
  message: string | null
}

export interface ReconciliationPlanProposeResponse {
  generated_at: string | null
  plan_artifact: string | null
  apply_supported: boolean
  planned_action_summary: Record<string, number>
  planned_actions: Record<string, unknown>[]
  audit_summary: Record<string, unknown>
  limitations: string[]
  message: string
}
