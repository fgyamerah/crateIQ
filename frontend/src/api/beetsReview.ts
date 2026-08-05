import { apiFetch } from './client'
import type { BeetsApplyResult, BeetsReviewResponse, BeetsReviewTrackUpdate } from '../types/beetsReview'

export const fetchBeetsReview = () => apiFetch.get<BeetsReviewResponse>('/enrichment/beets/review')
export const refreshBeetsReview = () => apiFetch.post<BeetsReviewResponse>('/enrichment/beets/preview-refresh', {})
export const updateBeetsReview = (trackId: number, body: BeetsReviewTrackUpdate) => apiFetch.patch<BeetsReviewResponse>(`/enrichment/beets/tracks/${trackId}`, body)
export const applyBeetsFields = (trackId: number, fields: Record<string, string>) => apiFetch.post<BeetsApplyResult>('/enrichment/beets/apply', { confirm: true, items: [{ track_id: trackId, fields }] })
