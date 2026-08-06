/**
 * Waveform lifecycle API client (W4).
 *
 * Mirrors the W3 backend contract exactly:
 *   GET    /api/tracks/{id}/waveform?resolution=...   read-only state/peaks
 *   POST   /api/tracks/{id}/waveform/generate         explicit generation only
 *   GET    /api/waveform-jobs/{jobId}                 job status
 *   DELETE /api/waveform-jobs/{jobId}                 best-effort cancellation
 *
 * The GET is side-effect free on the backend. Nothing in this module issues a
 * generation request implicitly — `requestWaveformGeneration` is only ever
 * called from an explicit user action.
 *
 * No response shape here carries a source path, cache path, or content hash;
 * the backend does not send them and the UI must not depend on them.
 */
import { apiFetch } from './client'

export type WaveformResolution = 'compact' | 'player' | 'detail'

/** Artifact lifecycle states returned by the waveform GET. */
export type WaveformArtifactStatus =
  | 'not_generated'
  | 'queued'
  | 'processing'
  | 'ready'
  | 'failed'
  | 'unsupported'
  | 'stale'
  | 'cancelled'

/** Job lifecycle states returned by the job endpoints. */
export type WaveformJobStatus = 'queued' | 'processing' | 'succeeded' | 'failed' | 'cancelled'

const ARTIFACT_STATUSES: readonly WaveformArtifactStatus[] = [
  'not_generated', 'queued', 'processing', 'ready',
  'failed', 'unsupported', 'stale', 'cancelled',
]

const JOB_STATUSES: readonly WaveformJobStatus[] = [
  'queued', 'processing', 'succeeded', 'failed', 'cancelled',
]

/** Job states after which polling must stop. */
export const TERMINAL_JOB_STATUSES: readonly WaveformJobStatus[] = ['succeeded', 'failed', 'cancelled']

export function isTerminalJobStatus(status: WaveformJobStatus): boolean {
  return TERMINAL_JOB_STATUSES.includes(status)
}

// ---------------------------------------------------------------------------
// Wire shapes (exactly what FastAPI serializes)
// ---------------------------------------------------------------------------

interface WaveformEncodingWire {
  type: string
  scale: number
  rendered_channels: number
}

interface WaveformResponseWire {
  track_id: number
  status: string
  job_id: string | null
  schema_version: number | null
  algorithm_version: string | null
  resolution: string | null
  duration_ms: number | null
  pair_count: number | null
  encoding: WaveformEncodingWire | null
  peaks: number[] | null
  generated_at: string | null
  error_code: string | null
}

interface WaveformGenerateResponseWire {
  track_id: number
  status: string
  job_id: string | null
  deduplicated: boolean
}

interface WaveformJobResponseWire {
  job_id: string
  track_id: number
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  cancel_requested: boolean
  error_code: string | null
}

// ---------------------------------------------------------------------------
// Discriminated union
//
// Only the `ready` variant exposes peaks/duration/scale, so the renderer
// cannot read peak data off a state that does not have any.
// ---------------------------------------------------------------------------

export interface WaveformReadyState {
  status: 'ready'
  trackId: number
  jobId: string | null
  resolution: WaveformResolution
  durationMs: number
  pairCount: number
  peaks: number[]
  scale: number
  generatedAt: string | null
}

export interface WaveformPendingState {
  status: Exclude<WaveformArtifactStatus, 'ready'>
  trackId: number
  jobId: string | null
  errorCode: string | null
}

export type WaveformState = WaveformReadyState | WaveformPendingState

export interface WaveformGenerationAck {
  trackId: number
  status: WaveformArtifactStatus
  jobId: string | null
  deduplicated: boolean
}

export interface WaveformJob {
  jobId: string
  trackId: number
  status: WaveformJobStatus
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  cancelRequested: boolean
  errorCode: string | null
}

// ---------------------------------------------------------------------------
// Normalizers — tolerate unexpected values rather than crashing the player
// ---------------------------------------------------------------------------

function toArtifactStatus(value: string): WaveformArtifactStatus {
  return (ARTIFACT_STATUSES as readonly string[]).includes(value)
    ? (value as WaveformArtifactStatus)
    : 'failed'
}

function toJobStatus(value: string): WaveformJobStatus {
  return (JOB_STATUSES as readonly string[]).includes(value)
    ? (value as WaveformJobStatus)
    : 'failed'
}

function toResolution(value: string | null): WaveformResolution {
  return value === 'compact' || value === 'detail' ? value : 'player'
}

export function normalizeWaveformResponse(wire: WaveformResponseWire): WaveformState {
  const status = toArtifactStatus(wire.status)
  if (
    status === 'ready'
    && Array.isArray(wire.peaks)
    && wire.peaks.length > 0
    && typeof wire.duration_ms === 'number'
  ) {
    return {
      status: 'ready',
      trackId: wire.track_id,
      jobId: wire.job_id,
      resolution: toResolution(wire.resolution),
      durationMs: wire.duration_ms,
      pairCount: wire.pair_count ?? Math.floor(wire.peaks.length / 2),
      peaks: wire.peaks,
      scale: wire.encoding?.scale && wire.encoding.scale > 0 ? wire.encoding.scale : 32767,
      generatedAt: wire.generated_at,
    }
  }
  return {
    // A `ready` status without usable peaks is treated as stale rather than
    // rendered as an empty waveform.
    status: status === 'ready' ? 'stale' : status,
    trackId: wire.track_id,
    jobId: wire.job_id,
    errorCode: wire.error_code,
  }
}

function normalizeJob(wire: WaveformJobResponseWire): WaveformJob {
  return {
    jobId: wire.job_id,
    trackId: wire.track_id,
    status: toJobStatus(wire.status),
    createdAt: wire.created_at,
    startedAt: wire.started_at,
    finishedAt: wire.finished_at,
    cancelRequested: wire.cancel_requested,
    errorCode: wire.error_code,
  }
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

/** Read-only. Never creates a job and never triggers extraction. */
export async function fetchTrackWaveform(
  trackId: number,
  resolution: WaveformResolution = 'player',
  signal?: AbortSignal,
): Promise<WaveformState> {
  const wire = await apiFetch.get<WaveformResponseWire>(
    `/tracks/${trackId}/waveform?resolution=${resolution}`,
    signal,
  )
  return normalizeWaveformResponse(wire)
}

/**
 * Explicit generation request. Call this only from a deliberate user action —
 * never from mount, route change, track change, or playback.
 */
export async function requestWaveformGeneration(
  trackId: number,
  force = false,
  signal?: AbortSignal,
): Promise<WaveformGenerationAck> {
  const wire = await apiFetch.post<WaveformGenerateResponseWire>(
    `/tracks/${trackId}/waveform/generate`,
    { force },
    signal,
  )
  return {
    trackId: wire.track_id,
    status: toArtifactStatus(wire.status),
    jobId: wire.job_id,
    deduplicated: wire.deduplicated,
  }
}

export async function fetchWaveformJob(jobId: string, signal?: AbortSignal): Promise<WaveformJob> {
  return normalizeJob(await apiFetch.get<WaveformJobResponseWire>(`/waveform-jobs/${jobId}`, signal))
}

export async function cancelWaveformJob(jobId: string, signal?: AbortSignal): Promise<WaveformJob> {
  return normalizeJob(await apiFetch.delete<WaveformJobResponseWire>(`/waveform-jobs/${jobId}`, signal))
}
