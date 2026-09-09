/**
 * Regression tests for the client-side waveform generation in-flight guard.
 *
 * The hook shares a module-level Set of track IDs with a generation POST in
 * flight, so concurrent hook instances (player + inspector, StrictMode
 * double-effects) must never POST twice for the same track. These tests pin
 * that behavior plus the preserved semantics: Retry, track switching, and
 * failed/unsupported states never auto-retrying.
 */
import { StrictMode, createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useTrackWaveform } from './useTrackWaveform'
import {
  fetchTrackWaveform,
  fetchWaveformJob,
  requestWaveformGeneration,
  type WaveformGenerationAck,
  type WaveformJob,
  type WaveformState,
} from '../api/waveforms'

vi.mock('../api/waveforms', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/waveforms')>()
  return {
    ...actual,
    fetchTrackWaveform: vi.fn(),
    fetchWaveformJob: vi.fn(),
    cancelWaveformJob: vi.fn(),
    requestWaveformGeneration: vi.fn(),
  }
})

type PendingStatus = 'not_generated' | 'stale' | 'cancelled' | 'failed' | 'unsupported'

function pendingState(trackId: number, status: PendingStatus): WaveformState {
  return { status, trackId, jobId: null, errorCode: null }
}

function readyAck(trackId: number): WaveformGenerationAck {
  return { trackId, status: 'ready', jobId: null, deduplicated: false }
}

function readyState(trackId: number): WaveformState {
  return {
    status: 'ready',
    trackId,
    jobId: null,
    resolution: 'player',
    durationMs: 1000,
    pairCount: 2,
    peaks: [0, 100, -100, 50],
    colorBands: null,
    scale: 32767,
    generatedAt: null,
  }
}

function jobState(trackId: number, status: WaveformJob['status']): WaveformJob {
  return {
    jobId: 'job-1',
    trackId,
    status,
    createdAt: '2026-09-04T00:00:00Z',
    startedAt: null,
    finishedAt: null,
    cancelRequested: false,
    errorCode: null,
  }
}

/** Flush pending promise continuations (safe under fake timers). */
const flush = () => act(async () => {
  for (let i = 0; i < 10; i += 1) await Promise.resolve()
})

const mockedFetch = vi.mocked(fetchTrackWaveform)
const mockedJob = vi.mocked(fetchWaveformJob)
const mockedGenerate = vi.mocked(requestWaveformGeneration)

beforeEach(() => {
  vi.clearAllMocks()
  mockedFetch.mockImplementation(async (trackId) => pendingState(trackId, 'not_generated'))
  mockedGenerate.mockImplementation(async (trackId) => readyAck(trackId))
})

describe('useTrackWaveform generation guard', () => {
  it('issues exactly one POST when two hook instances open the same track with the first POST deferred', async () => {
    let resolvePost!: (ack: WaveformGenerationAck) => void
    mockedGenerate.mockImplementation(
      () => new Promise<WaveformGenerationAck>((resolve) => { resolvePost = resolve }),
    )

    const first = renderHook(() => useTrackWaveform(1))
    const second = renderHook(() => useTrackWaveform(1))

    await waitFor(() => expect(mockedGenerate).toHaveBeenCalledTimes(1))
    // Let every read/observe continuation settle: no second POST may appear.
    await act(async () => { await Promise.resolve() })
    expect(mockedGenerate).toHaveBeenCalledTimes(1)

    resolvePost(readyAck(1))
    await waitFor(() => expect(first.result.current.generating).toBe(false))
    expect(second.result.current.generating).toBe(false)
    expect(mockedGenerate).toHaveBeenCalledTimes(1)
  })

  it('issues one POST for a single track with no waveform', async () => {
    renderHook(() => useTrackWaveform(7))
    await waitFor(() => expect(mockedGenerate).toHaveBeenCalledTimes(1))
    expect(mockedGenerate.mock.calls[0][0]).toBe(7)
    await act(async () => { await Promise.resolve() })
    expect(mockedGenerate).toHaveBeenCalledTimes(1)
  })

  it('does not duplicate the POST under StrictMode', async () => {
    renderHook(() => useTrackWaveform(3), {
      wrapper: ({ children }: { children: ReactNode }) => createElement(StrictMode, null, children),
    })
    await waitFor(() => expect(mockedGenerate).toHaveBeenCalledTimes(1))
    await act(async () => { await Promise.resolve() })
    expect(mockedGenerate).toHaveBeenCalledTimes(1)
  })

  it('issues one POST for the new track after a track switch', async () => {
    const { rerender } = renderHook(
      ({ trackId }) => useTrackWaveform(trackId),
      { initialProps: { trackId: 1 } },
    )
    await waitFor(() => expect(mockedGenerate).toHaveBeenCalledTimes(1))

    rerender({ trackId: 2 })
    await waitFor(() => expect(mockedGenerate).toHaveBeenCalledTimes(2))
    expect(mockedGenerate.mock.calls.map((call) => call[0])).toEqual([1, 2])
  })

  it('never auto-requests generation for a failed waveform', async () => {
    mockedFetch.mockImplementation(async (trackId) => pendingState(trackId, 'failed'))
    const { result } = renderHook(() => useTrackWaveform(4))
    await waitFor(() => expect(result.current.waveform?.status).toBe('failed'))
    await act(async () => { await Promise.resolve() })
    expect(mockedGenerate).not.toHaveBeenCalled()
  })

  it('allows an explicit retry after a failed generation request', async () => {
    mockedGenerate.mockRejectedValueOnce(new Error('network down'))
    const { result } = renderHook(() => useTrackWaveform(5))

    await waitFor(() => expect(mockedGenerate).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(result.current.actionError).toBe("Couldn't start waveform generation"))

    act(() => result.current.generate())
    await waitFor(() => expect(mockedGenerate).toHaveBeenCalledTimes(2))
    expect(mockedGenerate.mock.calls[1][0]).toBe(5)
  })

  it('never requests generation for an unsupported track', async () => {
    mockedFetch.mockImplementation(async (trackId) => pendingState(trackId, 'unsupported'))
    const { result } = renderHook(() => useTrackWaveform(6))
    await waitFor(() => expect(result.current.waveform?.status).toBe('unsupported'))
    await act(async () => { await Promise.resolve() })
    expect(mockedGenerate).not.toHaveBeenCalled()
  })

  it('lets the suppressed instance observe, attach to the job, and render ready — still exactly one POST', async () => {
    vi.useFakeTimers()
    try {
      let resolvePost!: (ack: WaveformGenerationAck) => void
      mockedGenerate.mockImplementation(
        () => new Promise<WaveformGenerationAck>((resolve) => { resolvePost = resolve }),
      )
      let phase: 'none' | 'queued' | 'ready' = 'none'
      mockedFetch.mockImplementation(async (trackId) => {
        if (phase === 'ready') return readyState(trackId)
        if (phase === 'queued') return { status: 'queued', trackId, jobId: 'job-1', errorCode: null }
        return pendingState(trackId, 'not_generated')
      })
      mockedJob.mockImplementation(async () => jobState(1, 'succeeded'))

      renderHook(() => useTrackWaveform(1)) // instance A: acquires the guard and POSTs
      const second = renderHook(() => useTrackWaveform(1)) // instance B: suppressed, observes
      await flush()
      expect(mockedGenerate).toHaveBeenCalledTimes(1)
      expect(second.result.current.waveform?.status).toBe('not_generated')

      // While the origin POST is in flight, B re-reads periodically without POSTing.
      await act(async () => { await vi.advanceTimersByTimeAsync(1600) })
      expect(mockedGenerate).toHaveBeenCalledTimes(1)

      // The origin POST completes with a queued job.
      phase = 'queued'
      await act(async () => {
        resolvePost({ trackId: 1, status: 'queued', jobId: 'job-1', deduplicated: false })
      })
      await flush()

      // B's next observation tick sees the job and attaches to normal polling.
      await act(async () => { await vi.advanceTimersByTimeAsync(1600) })
      expect(second.result.current.waveform?.status).toBe('queued')
      expect(second.result.current.generating).toBe(true)
      expect(mockedGenerate).toHaveBeenCalledTimes(1)

      // The job reaches a terminal state; B's polling re-reads and renders ready.
      phase = 'ready'
      await act(async () => { await vi.advanceTimersByTimeAsync(1600) })
      await flush()
      expect(second.result.current.waveform?.status).toBe('ready')
      expect(second.result.current.generating).toBe(false)
      expect(mockedGenerate).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('stops observing once the origin POST fails — no POST, no infinite loop', async () => {
    vi.useFakeTimers()
    try {
      let rejectPost!: (error: unknown) => void
      mockedGenerate.mockImplementation(
        () => new Promise<WaveformGenerationAck>((_resolve, reject) => { rejectPost = reject }),
      )

      const first = renderHook(() => useTrackWaveform(2))
      const second = renderHook(() => useTrackWaveform(2))
      await flush()
      expect(mockedGenerate).toHaveBeenCalledTimes(1)

      // The origin POST fails; the in-flight guard is released.
      await act(async () => { rejectPost(new Error('network down')) })
      await flush()
      expect(first.result.current.actionError).toBe("Couldn't start waveform generation")

      // B settles after its final re-read: no job, no POST, no retry.
      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
      expect(second.result.current.waveform?.status).toBe('not_generated')
      expect(second.result.current.generating).toBe(false)
      expect(mockedGenerate).toHaveBeenCalledTimes(1)

      // Observation has stopped: further time produces no further reads.
      const reads = mockedFetch.mock.calls.length
      await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
      expect(mockedFetch.mock.calls.length).toBe(reads)
    } finally {
      vi.useRealTimers()
    }
  })
})
