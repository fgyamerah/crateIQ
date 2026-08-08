import { apiFetch } from './client'
import type { AnalysisPreferences, LibraryRootValidation, LibrarySetupResult, PublishDestinationValidation, RuntimeReadiness, SettingsCapabilities, SettingsResponse } from '../types/settings'

export const fetchSettings = () => apiFetch.get<SettingsResponse>('/settings')
export const fetchSettingsRuntime = () => apiFetch.get<RuntimeReadiness>('/settings/runtime')
export const fetchSettingsCapabilities = () => apiFetch.get<SettingsCapabilities>('/settings/capabilities')
export const updateSettings = (body: {
  default_export_path_mode?: SettingsResponse['preferences']['default_export_path_mode']
  analysis?: Partial<AnalysisPreferences>
}) => apiFetch.patch<SettingsResponse>('/settings', body)
export const validateLibraryRoot = (library_root: string) => apiFetch.post<LibraryRootValidation>('/settings/library/validate', { library_root })
export const updateLibraryRoot = (library_root: string) => apiFetch.patch<SettingsResponse>('/settings/library', { library_root })
export const initializeLibrary = (library_root?: string) => apiFetch.post<LibrarySetupResult>('/settings/library/initialize', library_root ? { library_root } : {})
export const scanLibraryPreview = (library_root?: string) => apiFetch.post<LibrarySetupResult>('/library/scan-preview', library_root ? { library_root } : {})
export const importLibrary = (library_root?: string) => apiFetch.post<LibrarySetupResult>('/library/import', { ...(library_root ? { library_root } : {}), confirm: true })
export const validatePublishDestination = (destination: string) => apiFetch.post<PublishDestinationValidation>('/settings/publish-destination/validate', { destination })
export const updatePublishDestination = (destination: string) => apiFetch.patch<SettingsResponse>('/settings/publish-destination', { destination })
