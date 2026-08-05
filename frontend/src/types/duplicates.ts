export type DuplicateDecision = 'keep' | 'ignore' | 'review_later' | 'unresolved'

export interface DuplicateReviewItem {
  track_id: number
  filename: string
  title: string | null
  artist: string | null
  relative_path: string | null
  size_bytes: number | null
  decision: DuplicateDecision
  note: string
  reviewed_at: string | null
}

export interface DuplicateReviewGroup {
  group_id: string
  reason: string
  confidence: 'high' | 'medium' | 'low'
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
