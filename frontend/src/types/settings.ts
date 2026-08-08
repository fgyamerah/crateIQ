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
  supported_audio_files?: number
  total_files?: number
  folders_scanned?: number
  unsupported_file_count?: number
  skipped_file_count?: number
  importable?: boolean
  imported_count?: number
  existing_count?: number
  tags_read_count?: number
  total_indexed_count?: number
  sample_tracks?: string[]
  skipped_files?: string[]
  unsupported_files?: string[]
  duplicate_name_candidates?: Array<{ filename: string; count: number }>
  long_path_warnings?: string[]
  warnings?: string[]
  next_actions?: Array<{ label: string; route: string }>
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

export type CapabilityStatus = 'available' | 'missing' | 'unknown'
export type CapabilityActionState = 'ready' | 'setup_required' | 'coming_soon'

export interface WorkflowCapability {
  available: boolean
  status: CapabilityStatus
  purpose: string
  message: string
  action_state: CapabilityActionState
  required_tool?: string | null
  required_tools?: string[] | null
  required_source?: string | null
  enabled?: boolean | null
  locked?: boolean | null
}

export interface SettingsCapabilities {
  core: Record<string, WorkflowCapability>
  analysis: Record<string, WorkflowCapability>
  policies: {
    preserve_mik_values: boolean
    preserve_existing_bpm_key_cues: boolean
    missing_data_only: boolean
    no_tag_writes: boolean
  }
}

export interface AnalysisPreferences {
  analyze_bpm: boolean
  analyze_key: boolean
  use_mik_when_present: boolean
  preserve_existing_bpm_key_cues: boolean
  missing_data_only: boolean
  use_external_tools: boolean
}

export interface SettingsResponse {
  library: SettingsLibrary
  tools: SettingsTool[]
  safety: SettingsSafety
  preferences: {
    default_export_path_mode: 'filename' | 'relative' | 'absolute'
    analysis: AnalysisPreferences
  }
  capabilities: SettingsCapabilities
}

export interface RuntimeReadiness {
  status: RuntimeStatus
  checks: Array<{ name: string; status: CheckStatus; message: string; required: boolean; metadata: Record<string, unknown> }>
}
