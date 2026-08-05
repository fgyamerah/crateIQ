import { apiFetch } from './client'
import type { LibraryRootValidation, LibrarySetupResult, RuntimeReadiness, SettingsResponse } from '../types/settings'

export const fetchSettings = () => apiFetch.get<SettingsResponse>('/settings')
export const fetchSettingsRuntime = () => apiFetch.get<RuntimeReadiness>('/settings/runtime')
export const updateSettings = (default_export_path_mode: SettingsResponse['preferences']['default_export_path_mode']) => apiFetch.patch<SettingsResponse>('/settings', { default_export_path_mode })
export const validateLibraryRoot = (library_root: string) => apiFetch.post<LibraryRootValidation>('/settings/library/validate', { library_root })
export const updateLibraryRoot = (library_root: string) => apiFetch.patch<SettingsResponse>('/settings/library', { library_root })
export const initializeLibrary = (library_root?: string) => apiFetch.post<LibrarySetupResult>('/settings/library/initialize', library_root ? { library_root } : {})
export const scanLibraryPreview = (library_root?: string) => apiFetch.post<LibrarySetupResult>('/library/scan-preview', library_root ? { library_root } : {})
export const importLibrary = (library_root?: string) => apiFetch.post<LibrarySetupResult>('/library/import', { ...(library_root ? { library_root } : {}), confirm: true })
