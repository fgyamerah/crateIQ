import { apiFetch } from './client'
import type { CrateExportPreview, CrateExportRequest, CrateExportResult, ExportRunRequest, ExportRunResponse, RekordboxExportPreview, RekordboxExportRequest, RekordboxExportResult, SeratoExportPreview, SeratoExportRequest, SeratoExportResult, ValidateResponse } from '../types/export'
import type { CrateSummary } from '../types/crate'
import type { Job } from '../types/job'

export function validateExport(): Promise<ValidateResponse> {
  return apiFetch.post<ValidateResponse>('/exports/validate', {})
}

export function runExport(req: ExportRunRequest): Promise<ExportRunResponse> {
  return apiFetch.post<ExportRunResponse>('/exports/run', req)
}

export function fetchExports(limit = 20, offset = 0): Promise<Job[]> {
  return apiFetch.get<Job[]>(`/exports?limit=${limit}&offset=${offset}`)
}

export function fetchExport(id: string): Promise<Job> {
  return apiFetch.get<Job>(`/exports/${id}`)
}

export const fetchExportableCrates = () => apiFetch.get<CrateSummary[]>('/exports/crates')
export const previewCrateExport = (id: number, request: CrateExportRequest) => apiFetch.get<CrateExportPreview>(`/exports/crates/${id}/preview?format=${request.format}&path_mode=${request.path_mode}&include_metadata=${request.include_metadata}&line_endings=${request.line_endings}`)
export const exportCrate = (id: number, request: CrateExportRequest) => apiFetch.post<CrateExportResult>(`/exports/crates/${id}`, request)
export const previewSeratoExport = (id: number, pathMode: SeratoExportRequest['path_mode']) => apiFetch.get<SeratoExportPreview>(`/exports/serato/preview/${id}?path_mode=${pathMode}`)
export const exportSerato = (id: number, request: SeratoExportRequest) => apiFetch.post<SeratoExportResult>(`/exports/serato/${id}`, request)
export const previewRekordboxExport = (id: number, request: Pick<RekordboxExportRequest, 'path_mode' | 'include_metadata'>) => apiFetch.get<RekordboxExportPreview>(`/exports/rekordbox/preview/${id}?path_mode=${request.path_mode}&include_metadata=${request.include_metadata}`)
export const exportRekordbox = (id: number, request: RekordboxExportRequest) => apiFetch.post<RekordboxExportResult>(`/exports/rekordbox/${id}`, request)
