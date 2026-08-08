export type PublishExportTarget = 'csv' | 'json' | 'm3u' | 'm3u8' | 'rekordbox_xml' | 'serato'
// "library" is the only supported value: the active workspace's Library
// folder (or the legacy root itself in Legacy Direct Library compatibility
// mode). Never the managed Inbox or Quarantine.
export type PublishSyncSource = 'library'
export type PublishOperationStatus = 'running' | 'completed' | 'failed' | 'cancelled'
export type VerificationStatus = 'verified' | 'failed' | 'skipped'

export interface PublishReadiness {
  crate_id: number
  crate_name: string
  track_count: number
  export_target: PublishExportTarget
  export_destination_category: string
  export_ready: boolean
  sync_source: PublishSyncSource
  sync_destination_category: string
  sync_ready: boolean
  blockers: string[]
  warnings: string[]
  conflicts: string[]
  confirmation_required: boolean
  next_operation: 'export' | 'sync' | 'none'
}

export interface PublishExportPreview {
  crate_id: number
  crate_name: string
  export_target: PublishExportTarget
  target_path: string
  target_exists: boolean
  proposed_filename: string
  track_count: number
  warnings: string[]
  blockers: string[]
  no_overwrite: boolean
  confirmation_required: boolean
}

export interface PublishExportResult {
  operation_id: string
  crate_id: number
  crate_name: string
  export_target: PublishExportTarget
  written: boolean
  output_path: string
  track_count: number
  verification_status: VerificationStatus
  verification_details: string[]
  warnings: string[]
}

export interface PublishSyncFileChange {
  path: string
  is_dir: boolean
}

export interface PublishSyncPreview {
  sync_source: PublishSyncSource
  source_path: string
  dest_path: string
  ssd_mounted: boolean
  file_count: number
  files: PublishSyncFileChange[]
  truncated: boolean
  blockers: string[]
  warnings: string[]
  confirmation_required: boolean
}

export interface PublishSyncConfirmResponse {
  operation_id: string
  job_id: string
  message: string
}

export interface PublishSyncStatus {
  operation_id: string
  operation_type: 'sync'
  sync_source: string | null
  job_id: string | null
  status: PublishOperationStatus
  job_status: string | null
  progress_current: number | null
  progress_total: number | null
  progress_percent: number | null
  destination_relative: string | null
  result: string | null
  verification_status: VerificationStatus | null
  verification_details: string[]
  warnings: string[]
  error_reason: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface PublishOperationSummary {
  id: string
  operation_type: 'export' | 'sync'
  export_target: string | null
  sync_source: string | null
  status: PublishOperationStatus
  crate_id: number | null
  crate_name: string | null
  scope: string | null
  track_count: number
  destination_relative: string | null
  result: string | null
  verification_status: VerificationStatus | null
  verification_details: string[]
  warnings: string[]
  error_reason: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}
