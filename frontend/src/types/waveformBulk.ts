/**
 * Bulk "Generate missing waveforms" contract (Waveform Jobs Stage 2/3).
 * Mirrors backend/app/schemas/waveform.py's bulk models exactly.
 */

export interface WaveformBulkPreview {
  total_tracks: number
  ready: number
  missing: number
  generating: number
  failed: number
  unsupported: number
  eligible_to_generate: number
}

export interface WaveformBulkStart {
  id: string
  total_tracks: number
  eligible_total: number
}

export type WaveformBulkOperationStatus = 'running' | 'completed' | 'failed' | 'cancelled'

/** A persisted, app-owned record of one explicit, confirmed bulk run.
 * Candidate previews are never persisted -- only a confirmed run that began
 * work creates one of these. Lives in the backend's own jobs.db. */
export interface WaveformBulkOperation {
  id: string
  operation_type: 'generate_missing'
  status: WaveformBulkOperationStatus
  total_tracks: number
  eligible_total: number
  processed: number
  generated: number
  skipped: number
  failed: number
  remaining_missing: number | null
  cancel_requested: boolean
  error_reason: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface WaveformBulkHistory {
  history: WaveformBulkOperation[]
}
