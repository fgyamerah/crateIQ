import { apiFetch } from './client'
import type {
  PublishExportPreview,
  PublishExportResult,
  PublishExportTarget,
  PublishOperationSummary,
  PublishReadiness,
  PublishSyncConfirmResponse,
  PublishSyncPreview,
  PublishSyncSource,
  PublishSyncStatus,
} from '../types/publish'
import type { CratePathMode } from '../types/export'

type CrateLineEndings = 'lf' | 'crlf'

export function fetchPublishReadiness(
  crateId: number,
  exportTarget: PublishExportTarget,
  syncSource: PublishSyncSource,
): Promise<PublishReadiness> {
  return apiFetch.get<PublishReadiness>(
    `/publish/readiness/${crateId}?export_target=${exportTarget}&sync_source=${syncSource}`,
  )
}

export function previewPublishExport(
  crateId: number,
  exportTarget: PublishExportTarget,
  pathMode: CratePathMode = 'filename',
): Promise<PublishExportPreview> {
  return apiFetch.get<PublishExportPreview>(
    `/publish/export/${crateId}/preview?export_target=${exportTarget}&path_mode=${pathMode}`,
  )
}

export function confirmPublishExport(
  crateId: number,
  exportTarget: PublishExportTarget,
  pathMode: CratePathMode = 'filename',
  includeMetadata = true,
  lineEndings: CrateLineEndings = 'lf',
): Promise<PublishExportResult> {
  return apiFetch.post<PublishExportResult>(`/publish/export/${crateId}`, {
    export_target: exportTarget,
    path_mode: pathMode,
    include_metadata: includeMetadata,
    line_endings: lineEndings,
    confirm: true,
  })
}

export function previewPublishSync(syncSource: PublishSyncSource): Promise<PublishSyncPreview> {
  return apiFetch.post<PublishSyncPreview>('/publish/sync/preview', { sync_source: syncSource })
}

export function confirmPublishSync(syncSource: PublishSyncSource): Promise<PublishSyncConfirmResponse> {
  return apiFetch.post<PublishSyncConfirmResponse>('/publish/sync/confirm', {
    sync_source: syncSource,
    confirm: true,
  })
}

export function fetchPublishSyncStatus(operationId: string): Promise<PublishSyncStatus> {
  return apiFetch.get<PublishSyncStatus>(`/publish/sync/${operationId}`)
}

export function fetchPublishOperations(
  operationType?: 'export' | 'sync',
  limit = 20,
): Promise<PublishOperationSummary[]> {
  const query = operationType ? `?operation_type=${operationType}&limit=${limit}` : `?limit=${limit}`
  return apiFetch.get<PublishOperationSummary[]>(`/publish/operations${query}`)
}
