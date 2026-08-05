export type MetadataSourceCategory = 'local' | 'installed_tool' | 'external_api' | 'external_input'
export type MetadataConnectionStatus = 'not_tested' | 'unavailable' | 'ready' | 'failed' | 'not_implemented'

export interface MetadataSource {
  id: string; label: string; category: MetadataSourceCategory; enabled: boolean; configured: boolean
  requires_credentials: boolean; credentials_status: 'not_required' | 'missing' | 'saved' | 'invalid' | 'unknown'
  credential_fields: string[]; saved_credential_fields: string[]; connection_status: MetadataConnectionStatus
  priority: number; best_for: string[]; current_behavior: 'implemented' | 'preview_only' | 'settings_only' | 'planned'
  configuration_note?: string | null; safety: string[]
}
export interface MetadataSourcesResponse { sources: MetadataSource[] }
export interface MetadataSourceUpdate { id: string; enabled?: boolean; priority?: number; credentials?: Record<string, string | null> }
export interface MetadataSourceTestResult { source_id: string; connection_status: MetadataConnectionStatus; message: string; network_used: boolean }
export interface MetadataSourceClearResult { source_id: string; cleared: boolean; message: string }
