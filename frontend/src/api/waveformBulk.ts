import { apiFetch } from './client'
import type {
  WaveformBulkHistory,
  WaveformBulkOperation,
  WaveformBulkPreview,
  WaveformBulkStart,
} from '../types/waveformBulk'

/** Read-only. Never enqueues a job, runs FFmpeg, or writes any state. */
export const fetchWaveformBulkPreview = () =>
  apiFetch.get<WaveformBulkPreview>('/waveform-bulk/preview')

/**
 * Explicit bulk generation request. Call this only from a deliberate user
 * action -- never from mount, route change, or a background refresh.
 */
export const startWaveformBulkGenerate = () =>
  apiFetch.post<WaveformBulkStart>('/waveform-bulk/generate-missing', {})

export const fetchWaveformBulkHistory = () =>
  apiFetch.get<WaveformBulkHistory>('/waveform-bulk/operations')

export const fetchWaveformBulkOperation = (operationId: string) =>
  apiFetch.get<WaveformBulkOperation>(`/waveform-bulk/operations/${operationId}`)

export const cancelWaveformBulkOperation = (operationId: string) =>
  apiFetch.post<WaveformBulkOperation>(`/waveform-bulk/operations/${operationId}/cancel`, {})
