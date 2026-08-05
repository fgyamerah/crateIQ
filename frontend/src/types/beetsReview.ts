export type BeetsReviewDecision = 'pending' | 'applied' | 'ignored' | 'review_later'

export interface BeetsReviewItem {
  track_id: number
  filename: string
  relative_path: string | null
  current_fields: Record<string, string | null>
  missing_fields: string[]
  allowed_fields: string[]
  selected_fields: Record<string, string>
  decision: BeetsReviewDecision
  note: string
  updated_at: string | null
}

export interface BeetsReviewSummary {
  candidates: number
  pending: number
  applied: number
  ignored: number
  review_later: number
  fields_selected: number
}

export interface BeetsReviewResponse {
  summary: BeetsReviewSummary
  items: BeetsReviewItem[]
  safety: string[]
  warnings: string[]
  latest_preview_at: string | null
  source: string | null
  message: string | null
}

export interface BeetsReviewTrackUpdate {
  decision: BeetsReviewDecision
  note: string
  selected_fields: Record<string, string>
}

export interface BeetsApplyResult {
  applied: number
  skipped: number
  failed: number
  warnings: string[]
  review: BeetsReviewResponse
}
