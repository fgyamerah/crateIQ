export type RuntimeStatus = 'ready' | 'degraded' | 'not_ready'
export type CheckStatus = 'pass' | 'warn' | 'fail'

export interface SettingsLibrary {
  mode: 'demo' | 'configured'
  library_root: string
  processed_db: string
  manual_crates_db: string
  exports_root: string
  library_initialized: boolean
  pending_library_root: string | null
  pending_library_initialized: boolean
  restart_required: boolean
  restart_command: string
  readiness_status: RuntimeStatus
}

export interface LibraryRootValidation {
  library_root: string
  valid: boolean
  message: string
}

export interface LibrarySetupResult {
  library_root: string
  initialized?: boolean
  processed_db?: string
  track_count?: number
  imported_count?: number
  sample_tracks?: string[]
  skipped_files?: string[]
  unsupported_files?: string[]
  warnings?: string[]
  message: string
}

export interface SettingsTool {
  name: string
  status: CheckStatus
  message: string
  source: string
  resolved: string | null
}

export interface SettingsSafety {
  mixed_in_key_authoritative: boolean
  missing_data_only_analysis: boolean
  no_automatic_file_or_tag_modification: boolean
  no_live_serato_writes: boolean
  no_live_rekordbox_database_writes: boolean
  preview_before_export_or_apply: boolean
}

export interface SettingsResponse {
  library: SettingsLibrary
  tools: SettingsTool[]
  safety: SettingsSafety
  preferences: { default_export_path_mode: 'filename' | 'relative' | 'absolute' }
}

export interface RuntimeReadiness {
  status: RuntimeStatus
  checks: Array<{ name: string; status: CheckStatus; message: string; required: boolean; metadata: Record<string, unknown> }>
}
