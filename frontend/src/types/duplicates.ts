export type DuplicateDecision = 'keep' | 'ignore' | 'review_later' | 'unresolved'

export interface DuplicateReviewItem {
  track_id: number
  filename: string
  title: string | null
  artist: string | null
  relative_path: string | null
  size_bytes: number | null
  genre: string | null
  bpm: number | null
  key_camelot: string | null
  key_musical: string | null
  duration_sec: number | null
  format: string | null
  missing_metadata: string[]
  copy_marker: boolean
  decision: DuplicateDecision
  note: string
  reviewed_at: string | null
}

export interface DuplicateKeeperRecommendation {
  track_id: number | null
  reason_code: string
  evidence: string[]
}

export interface DuplicateReviewGroup {
  group_id: string
  reason: string
  confidence: 'high' | 'medium' | 'low'
  match_basis: string
  checksum_prefix: string | null
  recommendation: DuplicateKeeperRecommendation
  items: DuplicateReviewItem[]
}

export interface DuplicateReviewSummary {
  groups: number
  candidates: number
  unresolved: number
  keep: number
  ignore: number
  review_later: number
}

export interface DuplicateReviewResponse {
  summary: DuplicateReviewSummary
  groups: DuplicateReviewGroup[]
  safety: string[]
  warnings: string[]
  latest_preview_at: string | null
  source: string | null
  message: string | null
}

export interface DuplicateReviewDecisionUpdate {
  decision: DuplicateDecision
  note: string
}
