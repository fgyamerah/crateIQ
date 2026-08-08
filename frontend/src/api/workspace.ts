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

export function importToInbox(sourcePaths: string[]): Promise<WorkspaceImportResult> {
  return apiFetch.post<WorkspaceImportResult>('/workspace/import', { source_paths: sourcePaths, confirm: true })
}

export function fetchInboxTracks(params: { search?: string; limit?: number; offset?: number } = {}): Promise<InboxTrackPage> {
  const qs = new URLSearchParams()
  if (params.search) qs.set('search', params.search)
  qs.set('limit', String(params.limit ?? 100))
  qs.set('offset', String(params.offset ?? 0))
  return apiFetch.get<InboxTrackPage>(`/workspace/inbox/tracks?${qs}`)
}

export function previewPromotion(trackIds?: number[]): Promise<PromotionPreview> {
  return apiFetch.post<PromotionPreview>('/workspace/promotion/preview', { track_ids: trackIds ?? null })
}

export function applyPromotion(trackIds: number[]): Promise<PromotionApplyResult> {
  return apiFetch.post<PromotionApplyResult>('/workspace/promotion/apply', { track_ids: trackIds, confirm: true })
}
