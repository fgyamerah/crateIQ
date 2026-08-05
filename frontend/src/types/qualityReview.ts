export type QualityReviewDecision = 'reviewed' | 'ignore' | 'review_later' | 'unresolved'
export type QualityIssueFlag = 'unreadable' | 'missing_bitrate' | 'low_bitrate' | 'missing_duration' | 'unsupported_format' | 'probe_warning'

export interface QualityReviewItem {
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
  status: 'probe_ok' | 'unreadable' | 'missing_bitrate' | 'unsupported_format' | 'probe_warning'
  flags: QualityIssueFlag[]
  decision: QualityReviewDecision
  note: string
  reviewed_at: string | null
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
}
