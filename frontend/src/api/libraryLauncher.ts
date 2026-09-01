import { apiFetch } from './client'

export type LibraryClassification =
  | 'managed_workspace'
  | 'legacy_direct_library'
  | 'empty_folder'
  | 'external_music_folder'
  | 'malformed_or_unsafe'
  | 'missing'

export type ActivationStatus =
  | 'idle'
  | 'activating'
  | 'succeeded'
  | 'blocked'
  | 'failed'
  | 'fail_closed'

export interface RecentLibrary {
  library_id: string
  path: string
  display_name: string
  last_opened_at: string
  availability: boolean
  classification: LibraryClassification | string
  active: boolean
}

export interface LibraryRegistryResponse {
  recent_libraries: RecentLibrary[]
  registry_status: 'ready' | 'malformed'
  message: string | null
}

export interface CurrentLibraryResponse {
  rootless: boolean
  library_root: string | null
  library_id: string | null
  display_name: string | null
  launcher_status: 'ready' | 'supervisor_unavailable'
  activation_status: ActivationStatus
}

export interface ActivationStartResponse {
  activation_id: string
  activation_status: 'activating'
}

export interface ActivationBlocker {
  category: 'active_work' | 'foreign_active_work' | 'legacy_ambiguous_work'
  count: number
  message: string
}

export interface ActivationStatusResponse {
  activation_status: ActivationStatus
  activation_id: string | null
  library_id: string | null
  result: 'activated' | 'already_active' | null
  error_code: string | null
  message: string | null
  blocker: ActivationBlocker | null
  registry_recency_updated: boolean | null
  warning_code: 'registry_recency_update_failed' | null
}

export const fetchLibraryRegistry = (signal?: AbortSignal): Promise<LibraryRegistryResponse> =>
  apiFetch.get<LibraryRegistryResponse>('/launcher/library-registry', signal)

export const fetchCurrentLibrary = (signal?: AbortSignal): Promise<CurrentLibraryResponse> =>
  apiFetch.get<CurrentLibraryResponse>('/launcher/current-library', signal)

export const activateRegisteredLibrary = (
  libraryId: string,
  signal?: AbortSignal,
): Promise<ActivationStartResponse> =>
  apiFetch.post<ActivationStartResponse>('/launcher/activate-library', { library_id: libraryId }, signal)

export const fetchActivationStatus = (signal?: AbortSignal): Promise<ActivationStatusResponse> =>
  apiFetch.get<ActivationStatusResponse>('/launcher/activation-status', signal)
