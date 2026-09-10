import { apiFetch } from './client'
import type { BulkEnrichmentActionResult, BulkEnrichmentSummary, EnrichmentApplyResult, EnrichmentDecision, EnrichmentReview, InboxTrackEnrichmentReview } from '../types/enrichmentReview'
export const fetchEnrichmentReview=()=>apiFetch.get<EnrichmentReview>('/enrichment/review')
export const refreshEnrichmentReview=()=>apiFetch.post<EnrichmentReview>('/enrichment/review/preview-refresh',{})
export const updateEnrichmentSuggestion=(trackId:number,id:string,body:{decision:EnrichmentDecision;note:string;selected_fields:Record<string,string>})=>apiFetch.patch<EnrichmentReview>(`/enrichment/review/tracks/${trackId}/suggestions/${id}`,body)
export const applyEnrichmentSuggestion=(trackId:number,id:string,fields:Record<string,string>)=>apiFetch.post<EnrichmentApplyResult>('/enrichment/review/apply',{confirm:true,items:[{track_id:trackId,suggestion_id:id,fields}]})
export const runOnlineLookup=(trackId:number,source:'beets'|'musicbrainz')=>apiFetch.post<EnrichmentReview>(`/enrichment/review/tracks/${trackId}/online-lookup`,{source})
export const fetchInboxTrackEnrichmentReview=(trackId:number)=>apiFetch.get<InboxTrackEnrichmentReview>(`/workspace/inbox/tracks/${trackId}/enrichment-review`)
export const fetchBulkEnrichmentSummary=(trackIds:number[])=>apiFetch.post<BulkEnrichmentSummary>('/workspace/inbox/enrichment-review/summary',{track_ids:trackIds})
export const acceptSafeEnrichmentSuggestions=(trackIds:number[])=>apiFetch.post<BulkEnrichmentActionResult>('/workspace/inbox/enrichment-review/accept-safe',{track_ids:trackIds,confirm:true})
export const keepCurrentBulkEnrichment=(trackIds:number[])=>apiFetch.post<BulkEnrichmentActionResult>('/workspace/inbox/enrichment-review/keep-current',{track_ids:trackIds,confirm:true})
