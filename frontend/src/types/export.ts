import type { Job } from './job'

export type ExclusionCategory =
  | 'MISSING_ANALYSIS'
  | 'MISSING_METADATA'
  | 'STALE_DB'
  | 'JUNK_PLACEHOLDER'
  | 'BAD_PATH'
  | 'OTHER'

export type WarningLevel = 'info' | 'warning' | 'error'

export interface ExportWarning {
  level:   WarningLevel
  message: string
}

export interface ValidationStats {
  total_scanned:    number
  valid_count:      number
  invalid_count:    number
  missing_analysis: number
  missing_metadata: number
  stale_db:         number
  junk:             number
  other:            number
  by_category:      Record<string, number>
}

export interface ExcludedTrack {
  filepath:    string
  filename:    string
  artist:      string | null
  title:       string | null
  bpm:         number | null
  key_camelot: string | null
  genre:       string | null
  reasons:     string[]
  category:    ExclusionCategory
}

export interface ValidateResponse {
  stats:         ValidationStats
  warnings:      ExportWarning[]
  excluded:      ExcludedTrack[]
  truncated:     boolean
  output_paths:  Record<string, string>
}

export interface ExportRunRequest {
  dry_run:         boolean
  skip_m3u:        boolean
  force_xml:       boolean
  recover_missing: boolean
}

export interface ExportRunResponse {
  job_id:  string
  message: string
}

// Re-export for convenience
export type { Job }

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

export const CATEGORY_COLORS: Record<ExclusionCategory, string> = {
  MISSING_ANALYSIS: '#f59e0b',   // amber
  MISSING_METADATA: '#8b5cf6',   // violet
  STALE_DB:         '#ef4444',   // red
  JUNK_PLACEHOLDER: '#6b7280',   // gray
  BAD_PATH:         '#ec4899',   // pink
  OTHER:            '#6b7280',   // gray
}

export const CATEGORY_LABELS: Record<ExclusionCategory, string> = {
  MISSING_ANALYSIS: 'Missing Analysis',
  MISSING_METADATA: 'Missing Metadata',
  STALE_DB:         'Stale Path',
  JUNK_PLACEHOLDER: 'Junk File',
  BAD_PATH:         'Bad Path',
  OTHER:            'Other',
}
