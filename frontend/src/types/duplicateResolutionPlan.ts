export type DuplicateResolutionAction = 'keep' | 'candidate_for_reversible_resolution' | 'no_action' | 'review_required'

export interface DuplicateResolutionIdentityEvidence {
  type: 'checksum_prefix' | 'none'
  value: string | null
  note: string
}

export interface DuplicateResolutionObservedStat {
  size_bytes: number | null
  mtime_ns: string | null
}

export interface DuplicateResolutionExecutionRequirements {
  source_relative_path: string
  track_id: number
  identity_evidence: DuplicateResolutionIdentityEvidence
  observed_at_plan_time: DuplicateResolutionObservedStat
  proposed_destination_strategy: string
  collision_check_required: boolean
  backup_required: boolean
  restore_preconditions: string[]
  operation_ledger_linkage: string
}

export interface DuplicateResolutionItem {
  action_id: string
  track_id: number
  filename: string
  relative_path: string | null
  action: DuplicateResolutionAction
  blockers: string[]
  execution_requirements: DuplicateResolutionExecutionRequirements | null
}

export interface DuplicateResolutionGroup {
  group_id: string
  status: 'ready' | 'blocked'
  blockers: string[]
  keeper_track_id: number | null
  match_basis: string
  checksum_prefix: string | null
  items: DuplicateResolutionItem[]
}

export interface DuplicateResolutionSummary {
  groups: number
  ready_groups: number
  blocked_groups: number
  keep: number
  candidate_for_reversible_resolution: number
  no_action: number
  review_required: number
}

export interface DuplicateResolutionPlanResponse {
  generated_at: string | null
  latest_preview_at: string | null
  source: string | null
  apply_supported: boolean
  groups: DuplicateResolutionGroup[]
  summary: DuplicateResolutionSummary
  safety: string[]
  warnings: string[]
  message: string
}
