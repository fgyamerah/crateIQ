import { apiFetch } from './client'
import type { DuplicateReviewDecisionUpdate, DuplicateReviewResponse } from '../types/duplicates'

export const fetchDuplicateReview = () => apiFetch.get<DuplicateReviewResponse>('/duplicates/review')
export const refreshDuplicateReview = () => apiFetch.post<DuplicateReviewResponse>('/duplicates/review/preview-refresh', {})
export const updateDuplicateReviewDecision = (groupId: string, trackId: number, body: DuplicateReviewDecisionUpdate) =>
  apiFetch.patch<DuplicateReviewResponse>(`/duplicates/review/groups/${encodeURIComponent(groupId)}/items/${trackId}`, body)
