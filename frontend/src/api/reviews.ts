import { apiFetch } from './client'

export interface TrackReviewSummary {
  review_status: string
  rating: number | null
}

export type ReviewSummary = Record<string, TrackReviewSummary>

export async function fetchReviewSummary(ids: number[]): Promise<{ reviews: ReviewSummary }> {
  const unique = [...new Set(ids.filter(Number.isInteger))]
  if (!unique.length) return { reviews: {} }

  const batches: number[][] = []
  for (let index = 0; index < unique.length; index += 200) {
    batches.push(unique.slice(index, index + 200))
  }
  const responses = await Promise.all(batches.map((batch) => (
    apiFetch.get<{ reviews: ReviewSummary }>(`/reviews/summary?track_ids=${batch.join(',')}`)
  )))
  return { reviews: Object.assign({}, ...responses.map((response) => response.reviews)) }
}
