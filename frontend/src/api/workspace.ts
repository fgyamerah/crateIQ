import { apiFetch } from './client'
import type { TrackSummary } from '../types/track'

export interface WorkspaceStatus {
  state:            'managed_workspace' | 'legacy_direct_library' | 'not_configured'
  library_root:     string
  inbox_path:       string | null
  library_path:     string | null
  quarantine_path:  string | null
  marker_version:   number | null
  message:          string
}

export interface WorkspaceImportResult {
  library_root:           string
  sources_provided:       number
  audio_files_discovered: number
  imported_count:         number
  duplicate_count:        number
  failed_count:           number
  imported:  Array<{ source_filename: string; inbox_filename: string }>
  duplicates: Array<{ source_filename: string; reason: string }>
  failed:    Array<{ source_filename: string; reason: string }>
  warnings:  string[]
  message:   string
}

export interface InboxTrackPage {
  items:  TrackSummary[]
  limit:  number
  offset: number
  total:  number
}

export type InboxSortKey = 'artist' | 'title' | 'filename' | 'genre' | 'bpm' | 'key' | 'readiness'
export type SortOrder = 'asc' | 'desc'

export interface PromotionPreviewItem {
  track_id: number
  filename: string
  artist:   string | null
  title:    string | null
  genre:    string | null
  ready:    boolean
  blockers: string[]
  warnings: string[]
  destination_relative: string | null
  collision: 'identical' | 'conflict' | null
}

export interface PromotionPreview {
  library_root: string
  track_count:  number
  ready_count:  number
  blocked_count: number
  items: PromotionPreviewItem[]
  message: string
}

export interface PromotionApplyResult {
  library_root:   string
  promoted_count: number
  failed_count:   number
  results: Array<{ track_id: number; status: string; reason?: string; destination_relative?: string }>
}

export function fetchWorkspaceStatus(): Promise<WorkspaceStatus> {
  return apiFetch.get<WorkspaceStatus>('/workspace/status')
}

export function configureWorkspace(): Promise<WorkspaceStatus> {
  return apiFetch.post<WorkspaceStatus>('/workspace/configure', {})
}

export interface WorkspaceRootClassification extends WorkspaceStatus {
  exists: boolean
  parent_exists: boolean
  parent_writable: boolean | null
  can_create: boolean
}

/** Read-only: classify a candidate workspace root path. Never touches disk. */
export function classifyWorkspaceRoot(libraryRoot: string): Promise<WorkspaceRootClassification> {
  return apiFetch.post<WorkspaceRootClassification>('/workspace/root/classify', { library_root: libraryRoot })
}

/** Safely create only the final requested directory for a new workspace root. */
export function createWorkspaceRoot(libraryRoot: string): Promise<WorkspaceRootClassification> {
  return apiFetch.post<WorkspaceRootClassification>('/workspace/root/create', { library_root: libraryRoot, confirm: true })
}

export function importToInbox(sourcePaths: string[]): Promise<WorkspaceImportResult> {
  return apiFetch.post<WorkspaceImportResult>('/workspace/import', { source_paths: sourcePaths, confirm: true })
}

export function fetchInboxTracks(
  params: { search?: string; sort?: InboxSortKey; order?: SortOrder; limit?: number; offset?: number } = {},
): Promise<InboxTrackPage> {
  const qs = new URLSearchParams()
  if (params.search) qs.set('search', params.search)
  if (params.sort) qs.set('sort', params.sort)
  if (params.order) qs.set('order', params.order)
  qs.set('limit', String(params.limit ?? 100))
  qs.set('offset', String(params.offset ?? 0))
  return apiFetch.get<InboxTrackPage>(`/workspace/inbox/tracks?${qs}`)
}

// ---------------------------------------------------------------------------
// Inline Track/Artist/Genre editing + bulk edit
// ---------------------------------------------------------------------------

export interface InboxTrackRenameResult {
  track_id: number
  status: 'renamed' | 'no_change'
  filename: string
  filepath: string
}

export interface InboxTrackMetadataEditResult {
  track_id: number
  status: 'updated' | 'no_change'
  fields_changed: string[]
  artist: string | null
  genre: string | null
  tag_write: { written_count: number; failed_count: number; warnings: string[] } | null
}

export interface InboxTrackEditResponse {
  track_id: number
  rename: InboxTrackRenameResult | null
  metadata: InboxTrackMetadataEditResult | null
  errors: string[]
}

/** Single-track Inbox edit. `filename` is the basename only -- the extension is always locked to the current file. */
export function patchInboxTrack(
  trackId: number,
  fields: { filename?: string; artist?: string; genre?: string },
): Promise<InboxTrackEditResponse> {
  return apiFetch.patch<InboxTrackEditResponse>(`/workspace/inbox/tracks/${trackId}`, fields)
}

export interface InboxBulkEditFieldPreview {
  current_values: string[]
  new_value: string
}

export interface InboxBulkEditPreview {
  selected_count: number
  eligible_count: number
  skipped_not_inbox: number
  missing_count: number
  fields: { artist?: InboxBulkEditFieldPreview; genre?: InboxBulkEditFieldPreview }
  message: string
}

export function previewInboxBulkEdit(trackIds: number[], fields: { artist?: string; genre?: string }): Promise<InboxBulkEditPreview> {
  return apiFetch.post<InboxBulkEditPreview>('/workspace/inbox/bulk-edit/preview', { track_ids: trackIds, ...fields })
}

export interface InboxBulkEditResultItem {
  track_id: number
  status: 'succeeded' | 'unchanged' | 'skipped' | 'not_found' | 'failed'
  fields?: string[]
  reason?: string
}

export interface InboxBulkEditApplyResult {
  selected_count: number
  changed_count: number
  unchanged_count: number
  succeeded_count: number
  failed_count: number
  skipped_count: number
  not_found_count: number
  results: InboxBulkEditResultItem[]
  message: string
}

export function applyInboxBulkEdit(trackIds: number[], fields: { artist?: string; genre?: string }): Promise<InboxBulkEditApplyResult> {
  return apiFetch.post<InboxBulkEditApplyResult>('/workspace/inbox/bulk-edit/apply', { track_ids: trackIds, ...fields, confirm: true })
}

export function previewPromotion(trackIds?: number[]): Promise<PromotionPreview> {
  return apiFetch.post<PromotionPreview>('/workspace/promotion/preview', { track_ids: trackIds ?? null })
}

export function applyPromotion(trackIds: number[]): Promise<PromotionApplyResult> {
  return apiFetch.post<PromotionApplyResult>('/workspace/promotion/apply', { track_ids: trackIds, confirm: true })
}

// ---------------------------------------------------------------------------
// Batch preparation (Cycle 10)
// ---------------------------------------------------------------------------

export interface PreparePreflight {
  library_root: string
  inbox_total: number
  already_ready: number
  need_cleaning: number
  need_enrichment: number
  need_analysis: number
  likely_review: number
  unsupported_write_format: number
  enrichment_lookup_bound: number
  message: string
}

export interface PreparationOperation {
  id: string
  operation_type: 'process_all' | 'clean_selected' | 'enrich_selected'
  status: 'running' | 'completed' | 'failed' | 'cancelled'
  track_count: number
  cleaned_count: number
  enriched_count: number
  written_count: number
  needs_review_count: number
  ready_count: number
  failed_count: number
  cancel_requested: boolean
  warnings: string[]
  error_reason: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export function fetchPreparePreview(): Promise<PreparePreflight> {
  return apiFetch.get<PreparePreflight>('/workspace/prepare/preview')
}

export function startProcessAll(): Promise<{ operation_id: string; track_count: number }> {
  return apiFetch.post('/workspace/prepare/start', { confirm: true })
}

export function cleanSelected(trackIds: number[]): Promise<{ cleaned_count: number }> {
  return apiFetch.post('/workspace/prepare/clean', { track_ids: trackIds })
}

export function enrichSelected(trackIds: number[]): Promise<{ enriched_count: number; considered: number; warnings: string[] }> {
  return apiFetch.post('/workspace/prepare/enrich', { track_ids: trackIds })
}

export function fetchPrepareOperation(operationId: string): Promise<PreparationOperation> {
  return apiFetch.get<PreparationOperation>(`/workspace/prepare/operations/${operationId}`)
}

export function cancelPrepareOperation(operationId: string): Promise<PreparationOperation> {
  return apiFetch.post<PreparationOperation>(`/workspace/prepare/operations/${operationId}/cancel`, {})
}
