import { apiFetch } from './client'
export type ReviewSummary=Record<string,{review_status:string;rating:number|null}>
export const fetchReviewSummary=(ids:number[])=>{const unique=[...new Set(ids.filter(Number.isInteger))].slice(0,200);return unique.length?apiFetch.get<{reviews:ReviewSummary}>(`/reviews/summary?track_ids=${unique.join(',')}`):Promise.resolve({reviews:{}})}
