import { apiFetch } from './client'
import type { QualityReviewDecisionUpdate, QualityReviewResponse } from '../types/qualityReview'

export const fetchQualityReview = () => apiFetch.get<QualityReviewResponse>('/quality/review')
export const refreshQualityReview = () => apiFetch.post<QualityReviewResponse>('/quality/review/preview-refresh', {})
export const updateQualityReviewDecision = (trackId: number, body: QualityReviewDecisionUpdate) =>
  apiFetch.patch<QualityReviewResponse>(`/quality/review/tracks/${trackId}`, body)
