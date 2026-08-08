// "library" is the only supported value: the active workspace's Library
// folder (or the legacy root itself in Legacy Direct Library compatibility
// mode). Never the managed Inbox or Quarantine.
export type SyncSource = 'library'

export interface SyncFileChange {
  path:   string
  is_dir: boolean
}

export interface SyncPreviewRequest {
  source: SyncSource
}

export interface SyncPreviewResponse {
  source_path:  string
  dest_path:    string
  file_count:   number
  files:        SyncFileChange[]
  truncated:    boolean
  summary:      string | null
  warnings:     string[]
  ssd_mounted:  boolean
}

export interface SyncRunRequest {
  source:       SyncSource
  allow_delete: boolean
}

export interface SyncRunResponse {
  job_id:  string
  message: string
}

export type SyncDestinationStatus = 'ready' | 'needs_setup' | 'not_mounted' | 'unsafe'

export interface SyncConfigResponse {
  sources:              Record<string, string>   // name → resolved path
  dest:                 string | null             // null if not configured yet
  destination_status:   SyncDestinationStatus
  destination_blockers: string[]
  destination_warnings: string[]
  rsync_bin:            string
  ssd_mounted:          boolean
}
