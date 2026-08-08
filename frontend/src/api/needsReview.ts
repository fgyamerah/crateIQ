import { apiFetch } from './client'

export type NeedsReviewCategory = 'ALL' | 'METADATA' | 'IDENTITY_ENRICHMENT' | 'GENRE' | 'ANALYSIS' | 'QUALITY'

export interface NeedsReviewAction {
  label: string
  route: string
}

export interface NeedsReviewItem {
  track_id: number
  category: NeedsReviewCategory
  severity: 'HIGH' | 'MEDIUM' | 'LOW'
  reason_code: string
  summary: string
  current_value: unknown
  recommended_value: unknown
  confidence: string | null
  provenance: string | null
  actions: NeedsReviewAction[]
  filename: string | null
}

export interface NeedsReviewResponse {
  items: NeedsReviewItem[]
  counts: Record<string, number>
  message: string
}

export function fetchNeedsReview(category: NeedsReviewCategory = 'ALL'): Promise<NeedsReviewResponse> {
  return apiFetch.get<NeedsReviewResponse>(`/needs-review?category=${category}`)
}
