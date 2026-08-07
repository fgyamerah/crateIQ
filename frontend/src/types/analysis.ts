export type BpmReason =
  | 'missing_bpm'
  | 'too_low_10x'
  | 'likely_halved'
  | 'likely_doubled'
  | 'too_high'

export type AnomalyReviewStatus =
  | 'pending'
  | 'reviewed'
  | 'ignored'
  | 'requeued'
  | 'resolved'

export interface BpmAnomaly {
  id:                number
  track_id:          number
  filepath:          string
  artist:            string | null
  title:             string | null
  genre:             string | null
  current_bpm:       number | null
  suggested_bpm:     number | null
  reason:            BpmReason
  reason_label:      string
  review_status:     AnomalyReviewStatus
  detected_at:       string
  reviewed_at:       string | null
  review_note:       string | null
  reanalysis_job_id: string | null
}

export interface BpmCheckResult {
  tracks_scanned: number
  new_anomalies:  number
  resolved:       number
  total_active:   number
  items:          BpmAnomaly[]
}

export interface BpmSummary {
  by_status: Record<string, number>
  by_reason: Record<string, number>
}

export interface UpdateAnomalyRequest {
  review_status: AnomalyReviewStatus
  review_note?:  string
}

export interface ReanalyzeRequest {
  force:   boolean
  dry_run: boolean
}

export interface MikCoverageSummary {
  total_tracks: number
  with_bpm: number
  with_key: number
  with_camelot: number
  with_cues: number
  trusted_bpm: number
  trusted_key: number
  missing_bpm: number
  missing_key: number
  fallback_bpm_candidates: number
  fallback_key_candidates: number
}

export interface MikFinding {
  track_id: number
  filename: string
  bpm: number | null
  key_camelot: string | null
  key_musical: string | null
  source: 'mixed_in_key' | 'mik_compatible_tag' | 'existing_metadata' | 'filename_hint' | 'unknown'
  trusted: boolean
}

export interface MikCoverageResult {
  summary: MikCoverageSummary
  samples: MikFinding[]
  warnings: string[]
  cue_support: 'available' | 'unavailable' | 'not_implemented'
  write_behavior: 'crateiq_db_only'
}

export interface MikImportResult extends MikCoverageResult {
  imported_count: number
  unchanged_count: number
  skipped_count: number
}

export type AnalysisJobType =
  | 'mixed_in_key_coverage'
  | 'bpm_analysis'
  | 'key_analysis'
  | 'beets_enrichment'
  | 'duplicate_detection'
  | 'audio_quality_probe'

export type AnalysisJobStatus = 'ready' | 'missing_tool' | 'coming_soon' | 'disabled'

export interface AnalysisJobDefinition {
  type: AnalysisJobType
  label: string
  status: AnalysisJobStatus
  required_tools: string[]
  required_source?: string | null
  candidate_count: number
  enabled: boolean
  default_enabled: boolean
  runner_implemented: boolean
  write_behavior: string
  safety: string[]
  message: string
}

export interface AnalysisJobCandidate {
  track_id: number
  filename: string
  relative_path: string | null
  artist: string | null
  title: string | null
  genre: string | null
  bpm: number | null
  key_camelot: string | null
  key_musical: string | null
  missing_fields: string[]
}

export interface DuplicateCandidateItem {
  track_id: number
  filename: string
  title: string | null
  artist: string | null
  relative_path: string | null
  size_bytes: number | null
}

export interface DuplicateCandidateGroup {
  group_id: string
  reason: string
  confidence: 'high' | 'medium' | 'low'
  items: DuplicateCandidateItem[]
}

export interface QualityProbeResult {
  track_id: number
  filename: string
  relative_path: string | null
  status: 'probe_ok' | 'missing_bitrate' | 'unreadable' | 'unsupported_format' | 'probe_error'
  container: string | null
  codec: string | null
  duration_sec: number | null
  bitrate_kbps: number | null
  sample_rate_hz: number | null
  channels: number | null
  file_size_bytes: number | null
}

export interface AnalysisJobPreview {
  job: AnalysisJobDefinition
  total_tracks: number
  candidate_count: number
  samples: AnalysisJobCandidate[]
  warnings: string[]
  expected_write_behavior: string
  runner_implemented: boolean
  preview_only: boolean
  summary: Record<string, number> | null
  groups: DuplicateCandidateGroup[]
  quality_probes: QualityProbeResult[]
  next_step: string | null
}

export type AnalysisOperationStatus = 'running' | 'completed' | 'failed' | 'cancelled'

/** A persisted, app-owned record of one explicit, confirmed analysis run.
 * Candidate previews are never persisted -- only a confirmed run that began
 * work creates one of these. Lives in the backend's own jobs.db. */
export interface AnalysisOperation {
  id: string
  job_type: 'bpm_analysis' | 'key_analysis'
  mode: string
  status: AnalysisOperationStatus
  scope_limit: number
  eligible_total: number
  considered: number
  processed: number
  succeeded: number
  skipped: number
  failed: number
  remaining_missing: number | null
  cancel_requested: boolean
  error_reason: string | null
  warnings: string[]
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface AnalysisJobHistory {
  history: AnalysisOperation[]
  message: string
}

export interface BpmAnalysisRunResult {
  job_type: 'bpm_analysis'
  analyzed: number
  updated: number
  skipped: number
  failed: number
  remaining_missing_bpm: number
  warnings: string[]
  results: AnalysisJobCandidate[]
  operation_id: string | null
  cancelled: boolean
}

export interface KeyAnalysisRunResult {
  job_type: 'key_analysis'
  analyzed: number
  updated: number
  skipped: number
  failed: number
  remaining_missing_key: number
  warnings: string[]
  results: AnalysisJobCandidate[]
  operation_id: string | null
  cancelled: boolean
}

export const REASON_COLORS: Record<BpmReason, string> = {
  missing_bpm:    'reason--missing',
  too_low_10x:    'reason--critical',
  likely_halved:  'reason--warn',
  likely_doubled: 'reason--warn',
  too_high:       'reason--critical',
}

export const STATUS_COLORS: Record<AnomalyReviewStatus, string> = {
  pending:  'anomaly-status--pending',
  reviewed: 'anomaly-status--reviewed',
  ignored:  'anomaly-status--ignored',
  requeued: 'anomaly-status--requeued',
  resolved: 'anomaly-status--resolved',
}
