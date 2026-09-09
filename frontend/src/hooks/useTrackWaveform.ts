/**
 * Waveform state layer for the current player track (W4).
 *
 * Responsibilities: read waveform state, observe an active generation job,
 * expose an explicit generate action, and expose cancellation. It owns no
 * rendering and no audio — the persistent player remains the single source of
 * truth for playback.
 *
 * Safety contract:
 *   - The waveform GET is read-only and runs automatically.
 *   - Generation is POST-only. It starts automatically once per "track opened"
 *     when the read returns a no-valid-waveform state (`not_generated`,
 *     `stale`, or `cancelled`), and it also runs from the explicit `generate()`
 *     action (including retry after `failed`). It is never triggered merely by
 *     rendering a list of tracks, and `failed`/`unsupported` never auto-retry.
 *   - Every response is guarded by both an AbortController and a monotonic
 *     request token, so a late reply for a previous track can never overwrite
 *     the current one.
 *   - A module-level in-flight set ensures only one generation POST per track
 *     across all hook instances in the tab; concurrent instances observe the
 *     originating request (via the poll timer) until the job or the ready
 *     waveform is visible, and never POST themselves.
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
  type WaveformArtifactStatus,
  type WaveformResolution,
  type WaveformState,
} from '../api/waveforms'

/** Conservative poll interval while a generation job is queued or running. */
const JOB_POLL_INTERVAL_MS = 1500

/**
 * "No valid waveform exists" states that trigger automatic first-open
 * generation. `failed` and `unsupported` are deliberately excluded: they need
 * a controlled retry rather than an automatic loop.
 */
const AUTO_TRIGGER_STATUSES: ReadonlySet<WaveformArtifactStatus> = new Set([
  'not_generated',
  'stale',
  'cancelled',
])

/**
 * Track IDs with a waveform generation POST currently in flight, shared
 * across every hook instance in this tab. A second instance (or a StrictMode
 * double-effect) that would trigger generation for a track already being
 * requested must not POST again; it observes existing state instead. The
 * backend-side deduplication remains as the cross-tab safety net.
 */
const generationRequestsInFlight = new Set<number>()

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

  /**
   * Observe another instance's in-flight generation request without POSTing.
   * Re-reads the waveform state on the poll timer while the originating POST
   * is still in flight; once a queued/processing job is visible it hands off
   * to normal job polling, and a visible `ready` state simply renders. When
   * the originating POST finishes without a visible job, exactly one final
   * re-read settles the state — observation never turns into generation.
   * Uses the shared poll timer, so track change/unmount cleanup is unchanged.
   */
  const observeGeneration = useCallback((forTrackId: number, token: number) => {
    stopPolling()
    let finalReadDone = false
    const tick = async () => {
      if (!isCurrent(token, forTrackId)) return
      const signal = abortRef.current?.signal
      if (!signal) return
      const state = await readWaveform(forTrackId, token, signal)
      if (!isCurrent(token, forTrackId) || !state) return
      if (state.status === 'ready') {
        stopPolling()
        setGenerating(false)
        return
      }
      if (state.jobId && (state.status === 'queued' || state.status === 'processing')) {
        // The job created by the originating instance is visible: attach.
        setGenerating(true)
        pollJob(forTrackId, token, state.jobId)
        return
      }
      if (generationRequestsInFlight.has(forTrackId)) {
        // The originating POST is still in flight: keep observing.
        pollTimerRef.current = setTimeout(() => void tick(), JOB_POLL_INTERVAL_MS)
        return
      }
      if (!finalReadDone) {
        // The POST finished during the read above, so that read may predate
        // its completion. Re-read exactly once more before settling.
        finalReadDone = true
        pollTimerRef.current = setTimeout(() => void tick(), JOB_POLL_INTERVAL_MS)
        return
      }
      // Settled with no active job (failed/unsupported/not_generated): stop.
      // This is never an automatic retry — only the explicit generate()
      // action may issue another POST.
      setGenerating(false)
    }
    pollTimerRef.current = setTimeout(() => void tick(), JOB_POLL_INTERVAL_MS)
  }, [isCurrent, pollJob, readWaveform, stopPolling])

  /**
   * Issue the generation POST. Shared by the explicit `generate` action and the
   * automatic first-open trigger. A module-level in-flight set prevents
   * duplicate POSTs from concurrent hook instances in this tab; the backend
   * additionally deduplicates concurrent requests for the same track, so this
   * remains safe across surfaces/tabs.
   */
  const startGeneration = useCallback((forTrackId: number, token: number) => {
    setActionError(null)

    // Another hook instance is already requesting generation for this track:
    // do not POST again. Observe its progress until the job or the ready
    // waveform becomes visible instead.
    if (generationRequestsInFlight.has(forTrackId)) {
      observeGeneration(forTrackId, token)
      return
    }

    setGenerating(true)
    generationRequestsInFlight.add(forTrackId)

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
      } finally {
        // Always release the track, even on abort/track switch, so a later
        // explicit retry or remount is allowed to POST again.
        generationRequestsInFlight.delete(forTrackId)
      }
    })()
  }, [isCurrent, observeGeneration, pollJob, readWaveform])

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
      } else if (state && !state.jobId
          && AUTO_TRIGGER_STATUSES.has(state.status as WaveformArtifactStatus)) {
        // First open with no valid waveform: enqueue generation automatically.
        // This is a deliberate one-shot POST from a narrow "track opened"
        // context (player/inspector/review), never from rendering a list.
        startGeneration(trackId, token)
      }
    })()

    return () => {
      controller.abort()
      stopPolling()
    }
  }, [trackId, isCurrent, pollJob, readWaveform, startGeneration, stopPolling])

  /** Explicit user action (manual generate or retry after failure). */
  const generate = useCallback(() => {
    const forTrackId = activeTrackRef.current
    if (forTrackId === null || generating) return
    startGeneration(forTrackId, requestTokenRef.current)
  }, [generating, startGeneration])

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
