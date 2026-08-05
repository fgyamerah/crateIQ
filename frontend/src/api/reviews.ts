import { apiFetch } from './client'

export interface TrackReviewSummary {
  review_status: string
  rating: number | null
}

export type ReviewSummary = Record<string, TrackReviewSummary>

export function fetchReviewSummary(ids: number[]): Promise<{ reviews: ReviewSummary }> {
  const unique = [...new Set(ids.filter(Number.isInteger))].slice(0, 200)
  return unique.length
    ? apiFetch.get<{ reviews: ReviewSummary }>(`/reviews/summary?track_ids=${unique.join(',')}`)
    : Promise.resolve({ reviews: {} })
}
