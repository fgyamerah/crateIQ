import { apiFetch } from './client'
import type { CrateExportPreview, CrateExportRequest, CrateExportResult, ExportRunRequest, ExportRunResponse, ValidateResponse } from '../types/export'
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
