import { apiFetch } from './client'
import type { MetadataSourceClearResult, MetadataSourceTestResult, MetadataSourceUpdate, MetadataSourcesResponse } from '../types/metadataSources'

export const fetchMetadataSources = () => apiFetch.get<MetadataSourcesResponse>('/settings/metadata-sources')
export const updateMetadataSources = (sources: MetadataSourceUpdate[]) => apiFetch.patch<MetadataSourcesResponse>('/settings/metadata-sources', { sources })
export const testMetadataSource = (sourceId: string) => apiFetch.post<MetadataSourceTestResult>(`/settings/metadata-sources/${sourceId}/test`, {})
export const clearMetadataSourceCredentials = (sourceId: string) => apiFetch.post<MetadataSourceClearResult>(`/settings/metadata-sources/${sourceId}/clear-credentials`, {})
