export type TagWriteAction = 'ADD' | 'REPLACE'

export interface TagWritePlanField {
  field: string
  current_file_value: string | null
  approved_value: string
  action: TagWriteAction
}

export interface TagWritePlanItem {
  track_id: number
  filename: string | null
  relative_path: string | null
  blocked: boolean
  blocker: string | null
  fields: TagWritePlanField[]
  expected_size?: number
  /** JSON string: nanosecond epoch timestamps exceed Number.MAX_SAFE_INTEGER. */
  expected_mtime_ns?: string
}

export interface TagWritePlan {
  items: TagWritePlanItem[]
  track_count: number
  changeable_count: number
  no_op_count: number
  blocked_count: number
  additions: number
  replacements: number
  backup_space_estimate_bytes: number
  writable_fields: string[]
  supported_formats: string[]
  message: string
}

export interface TagWriteApplyResultItem {
  track_id: number
  status: 'applied' | 'skipped' | 'failed'
  reason?: string
  fields?: string[]
  verified?: boolean
  restored?: boolean
  restore_verified?: boolean
}

export type TagWriteOperationStatus = 'previewed' | 'running' | 'completed' | 'failed' | 'partially_failed' | 'restored'

export interface TagWriteApplyResult {
  operation_id: string
  status: TagWriteOperationStatus
  applied: number
  skipped: number
  failed: number
  results: TagWriteApplyResultItem[]
}

export interface TagWriteOperation {
  id: string
  status: TagWriteOperationStatus
  track_count: number
  applied_count: number
  skipped_count: number
  failed_count: number
  plan: TagWritePlanItem[]
  backup_manifest: Array<{ track_id: number; relative_path: string; backup_filename: string; original_sha256: string }>
  results: TagWriteApplyResultItem[]
  warnings: string[]
  error_reason: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  restored_at: string | null
}
