import { apiFetch } from './client'

export interface TrackReviewSummary {
  review_status: string
  rating: number | null
  favorite: boolean
}

export type ReviewSummary = Record<string, TrackReviewSummary>

export interface TrackReviewUpdate {
  rating?: number | null
  favorite?: boolean
  review_status?: string
  notes?: string
}

export interface ReviewSignalOperation {
  operation: 'set' | 'clear'
  value?: number | boolean
}

export interface ReviewSignalPreview {
  selected_count: number
  eligible_count: number
  changeable_count: number
  missing_count: number
  fields: Partial<Record<'rating' | 'favorite', {
    operation: 'set' | 'clear'
    value: number | boolean | null
    mixed: boolean
    affected_count: number
    already_matching_count: number
    skipped_count: number
  }>>
  items: Array<{ track_id: number; status: 'change' | 'unchanged' | 'not_found' }>
  message: string
}

export interface ReviewSignalApplyResult {
  selected_count: number
  changed_count: number
  succeeded_count: number
  unchanged_count: number
  results: Array<{ track_id: number; status: 'succeeded' | 'unchanged'; fields?: string[] }>
  message: string
}

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

export function updateTrackReview(trackId: number, update: TrackReviewUpdate) {
  return apiFetch.patch<TrackReviewSummary & { track_id: number }>(`/reviews/tracks/${trackId}`, update)
}

export function previewReviewSignals(trackIds: number[], operations: Partial<Record<'rating' | 'favorite', ReviewSignalOperation>>) {
  return apiFetch.post<ReviewSignalPreview>('/reviews/signals/preview', { track_ids: trackIds, operations })
}

export function applyReviewSignals(trackIds: number[], operations: Partial<Record<'rating' | 'favorite', ReviewSignalOperation>>) {
  return apiFetch.post<ReviewSignalApplyResult>('/reviews/signals/apply', { track_ids: trackIds, operations, confirm: true })
}
