/**
 * Waveform state layer for the current player track (W4).
 *
 * Responsibilities: read waveform state, observe an active generation job,
 * expose an explicit generate action, and expose cancellation. It owns no
 * rendering and no audio — the persistent player remains the single source of
 * truth for playback.
 *
 * Safety contract:
 *   - The waveform GET runs automatically; generation never does. Nothing here
 *     issues a POST except `generate()`, which is only reachable from a user
 *     action.
 *   - Every response is guarded by both an AbortController and a monotonic
 *     request token, so a late reply for a previous track can never overwrite
 *     the current one.
 *   - Polling is a self-cancelling timeout chain that stops on any terminal
 *     job state, on track change, and on unmount.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import {
  cancelWaveformJob,
  fetchTrackWaveform,
  fetchWaveformJob,
  isTerminalJobStatus,
  requestWaveformGeneration,
  type WaveformResolution,
  type WaveformState,
} from '../api/waveforms'

/** Conservative poll interval while a generation job is queued or running. */
const JOB_POLL_INTERVAL_MS = 1500

export interface UseTrackWaveformResult {
  /** Null until the first read for the current track resolves. */
  waveform: WaveformState | null
  loading: boolean
  /** True between requesting generation and the job reaching a terminal state. */
  generating: boolean
  /** True when the backend reports generation is unavailable. */
  generationUnavailable: boolean
  /** Short user-facing message for a failed generate/cancel action. */
  actionError: string | null
  /** Explicit user action. The only path that issues a generation POST. */
  generate: () => void
  /** Cancel the active generation job, if any. */
  cancel: () => void
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

export function useTrackWaveform(
  trackId: number | null,
  resolution: WaveformResolution = 'player',
): UseTrackWaveformResult {
  const [waveform, setWaveform] = useState<WaveformState | null>(null)
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [generationUnavailable, setGenerationUnavailable] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  // Monotonic token: only the newest request for the newest track may commit.
  const requestTokenRef = useRef(0)
  const activeTrackRef = useRef<number | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  /** A response may only be applied if it is still the newest for this track. */
  const isCurrent = useCallback(
    (token: number, forTrackId: number) =>
      mountedRef.current
      && token === requestTokenRef.current
      && forTrackId === activeTrackRef.current,
    [],
  )

  const readWaveform = useCallback(async (
    forTrackId: number,
    token: number,
    signal: AbortSignal,
  ): Promise<WaveformState | null> => {
    try {
      const next = await fetchTrackWaveform(forTrackId, resolution, signal)
      if (!isCurrent(token, forTrackId)) return null
      setWaveform(next)
      return next
    } catch (error) {
      if (isAbortError(error)) return null
      if (!isCurrent(token, forTrackId)) return null
      // A waveform read failure must never look like a playback failure.
      setWaveform({ status: 'failed', trackId: forTrackId, jobId: null, errorCode: null })
      return null
    }
  }, [isCurrent, resolution])

  /** Poll one job until it reaches a terminal state, then re-read the waveform. */
  const pollJob = useCallback((forTrackId: number, token: number, jobId: string) => {
    stopPolling()
    const tick = async () => {
      if (!isCurrent(token, forTrackId)) return
      try {
        const job = await fetchWaveformJob(jobId, abortRef.current?.signal)
        if (!isCurrent(token, forTrackId)) return
        if (isTerminalJobStatus(job.status)) {
          stopPolling()
          setGenerating(false)
          // The job endpoint never carries peaks; the waveform GET is
          // authoritative for the published artifact.
          const signal = abortRef.current?.signal
          if (signal) await readWaveform(forTrackId, token, signal)
          return
        }
        setWaveform((current) => {
          if (!current || current.trackId !== forTrackId) return current
          if (current.status === 'ready') return current
          return { status: job.status === 'processing' ? 'processing' : 'queued', trackId: forTrackId, jobId, errorCode: null }
        })
        pollTimerRef.current = setTimeout(() => void tick(), JOB_POLL_INTERVAL_MS)
      } catch (error) {
        if (isAbortError(error)) return
        // Stop polling rather than hammering a failing endpoint.
        stopPolling()
        if (isCurrent(token, forTrackId)) setGenerating(false)
      }
    }
    pollTimerRef.current = setTimeout(() => void tick(), JOB_POLL_INTERVAL_MS)
  }, [isCurrent, readWaveform, stopPolling])

  // Track change: abort everything, clear to a deterministic empty state, read.
  useEffect(() => {
    abortRef.current?.abort()
    stopPolling()
    setGenerating(false)
    setActionError(null)

    const token = requestTokenRef.current + 1
    requestTokenRef.current = token
    activeTrackRef.current = trackId

    if (trackId === null) {
      setWaveform(null)
      setLoading(false)
      abortRef.current = null
      return
    }

    const controller = new AbortController()
    abortRef.current = controller
    setWaveform(null)
    setLoading(true)

    void (async () => {
      const state = await readWaveform(trackId, token, controller.signal)
      if (!isCurrent(token, trackId)) return
      setLoading(false)
      // Observing an already-active job is a read, not a generation request.
      if (state && state.status !== 'ready' && state.jobId
          && (state.status === 'queued' || state.status === 'processing')) {
        setGenerating(true)
        pollJob(trackId, token, state.jobId)
      }
    })()

    return () => {
      controller.abort()
      stopPolling()
    }
  }, [trackId, isCurrent, pollJob, readWaveform, stopPolling])

  const generate = useCallback(() => {
    const forTrackId = activeTrackRef.current
    if (forTrackId === null || generating) return
    const token = requestTokenRef.current
    setActionError(null)
    setGenerating(true)

    void (async () => {
      try {
        const ack = await requestWaveformGeneration(forTrackId, false, abortRef.current?.signal)
        if (!isCurrent(token, forTrackId)) return
        if (ack.status === 'ready' || !ack.jobId) {
          setGenerating(false)
          const signal = abortRef.current?.signal
          if (signal) await readWaveform(forTrackId, token, signal)
          return
        }
        // Respects W3 deduplication: an existing active job is simply attached to.
        setWaveform({ status: ack.status === 'processing' ? 'processing' : 'queued', trackId: forTrackId, jobId: ack.jobId, errorCode: null })
        pollJob(forTrackId, token, ack.jobId)
      } catch (error) {
        if (isAbortError(error)) return
        if (!isCurrent(token, forTrackId)) return
        setGenerating(false)
        if (error instanceof ApiError && error.status === 503) {
          setGenerationUnavailable(true)
          setActionError('Waveform generation is unavailable')
        } else if (error instanceof ApiError && error.status === 429) {
          setActionError('Waveform queue is busy — try again shortly')
        } else if (error instanceof ApiError && error.status === 413) {
          setActionError('This file is too large for waveform generation')
        } else {
          setActionError("Couldn't start waveform generation")
        }
      }
    })()
  }, [generating, isCurrent, pollJob, readWaveform])

  const cancel = useCallback(() => {
    const forTrackId = activeTrackRef.current
    const jobId = waveform && waveform.status !== 'ready' ? waveform.jobId : null
    if (forTrackId === null || !jobId) return
    const token = requestTokenRef.current
    setActionError(null)

    void (async () => {
      try {
        await cancelWaveformJob(jobId, abortRef.current?.signal)
        if (!isCurrent(token, forTrackId)) return
        stopPolling()
        setGenerating(false)
        const signal = abortRef.current?.signal
        if (signal) await readWaveform(forTrackId, token, signal)
      } catch (error) {
        if (isAbortError(error)) return
        if (!isCurrent(token, forTrackId)) return
        setActionError("Couldn't cancel waveform generation")
      }
    })()
  }, [isCurrent, readWaveform, stopPolling, waveform])

  // Unmount: abort in-flight work and clear timers so nothing updates later.
  useEffect(() => () => {
    abortRef.current?.abort()
    stopPolling()
  }, [stopPolling])

  return { waveform, loading, generating, generationUnavailable, actionError, generate, cancel }
}
