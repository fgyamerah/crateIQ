export type QualityReviewDecision = 'reviewed' | 'ignore' | 'review_later' | 'unresolved'
export type QualityIssueFlag = 'unreadable' | 'missing_bitrate' | 'low_bitrate' | 'missing_duration' | 'unsupported_format' | 'probe_warning'
export type QualityFindingSeverity = 'warning' | 'error'

export interface QualityReviewItem {
  finding_key: string | null
  track_id: number
  filename: string
  title: string | null
  artist: string | null
  relative_path: string | null
  container: string | null
  codec: string | null
  duration_sec: number | null
  bitrate_kbps: number | null
  sample_rate_hz: number | null
  channels: number | null
  file_size_bytes: number | null
  status: 'probe_ok' | 'unreadable' | 'missing_bitrate' | 'unsupported_format' | 'probe_warning' | null
  flags: QualityIssueFlag[]
  // Durable (non-ffprobe) finding fields -- null/false for ffprobe-snapshot items.
  reason_code: string | null
  source: string | null
  severity: QualityFindingSeverity | null
  blocking: boolean
  message: string | null
  details: Record<string, unknown> | null
  first_seen_at: string | null
  last_seen_at: string | null
  occurrence_count: number | null
  decision: QualityReviewDecision
  note: string
  reviewed_at: string | null
  // True only for an active audio_decode_failed finding whose track
  // currently has an automatic-retry pause.
  retry_paused: boolean
}

export interface QualityReviewSummary {
  tracks_checked: number
  findings: number
  unresolved: number
  reviewed: number
  ignored: number
  review_later: number
}

export interface QualityReviewResponse {
  summary: QualityReviewSummary
  items: QualityReviewItem[]
  safety: string[]
  warnings: string[]
  latest_preview_at: string | null
  source: string | null
  low_bitrate_threshold_kbps: number
  message: string | null
}

export interface QualityReviewDecisionUpdate {
  decision: QualityReviewDecision
  note: string
  finding_key?: string | null
}
