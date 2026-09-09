/**
 * Tests for the bulk waveform operation poller in WaveformGenerationCard.
 *
 * Pins the adaptive cadence (1 s while starting, 2.5 s while active,
 * 5 s steady-state), terminal-state stops, unmount cleanup, single-stream
 * behavior across remounts, and hidden-tab pausing with immediate resume.
 */
import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import WaveformGenerationCard from './WaveformGenerationCard'
import {
  cancelWaveformBulkOperation,
  fetchWaveformBulkHistory,
  fetchWaveformBulkOperation,
  fetchWaveformBulkPreview,
  startWaveformBulkGenerate,
} from '../../api/waveformBulk'
import type { WaveformBulkOperation, WaveformBulkPreview } from '../../types/waveformBulk'

vi.mock('../../api/waveformBulk', () => ({
  fetchWaveformBulkPreview: vi.fn(),
  fetchWaveformBulkHistory: vi.fn(),
  fetchWaveformBulkOperation: vi.fn(),
  startWaveformBulkGenerate: vi.fn(),
  cancelWaveformBulkOperation: vi.fn(),
}))

const mockedPreview = vi.mocked(fetchWaveformBulkPreview)
const mockedHistory = vi.mocked(fetchWaveformBulkHistory)
const mockedOperation = vi.mocked(fetchWaveformBulkOperation)
const mockedStart = vi.mocked(startWaveformBulkGenerate)
const mockedCancel = vi.mocked(cancelWaveformBulkOperation)

function preview(overrides: Partial<WaveformBulkPreview> = {}): WaveformBulkPreview {
  return {
    total_tracks: 10,
    ready: 0,
    missing: 10,
    generating: 0,
    failed: 0,
    unsupported: 0,
    eligible_to_generate: 10,
    ...overrides,
  }
}

function operation(overrides: Partial<WaveformBulkOperation> = {}): WaveformBulkOperation {
  return {
    id: 'op-1',
    operation_type: 'generate_missing',
    status: 'running',
    total_tracks: 10,
    eligible_total: 10,
    processed: 0,
    generated: 0,
    skipped: 0,
    failed: 0,
    remaining_missing: 10,
    cancel_requested: false,
    error_reason: null,
    created_at: '2026-09-05T00:00:00Z',
    started_at: '2026-09-05T00:00:00Z',
    finished_at: null,
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve
    reject = nextReject
  })
  return { promise, resolve, reject }
}

/** Flush pending promise continuations (safe under fake timers). */
const flush = () => act(async () => {
  for (let i = 0; i < 10; i += 1) await Promise.resolve()
})

const advance = (ms: number) => act(async () => {
  await vi.advanceTimersByTimeAsync(ms)
})

/** Render the card with a running operation resumed from history. */
async function renderWithRunningOperation(op: WaveformBulkOperation = operation()) {
  mockedHistory.mockResolvedValue({ history: [op] })
  const view = render(<WaveformGenerationCard />)
  await flush()
  return view
}

function setVisibility(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => (hidden ? 'hidden' : 'visible'),
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  setVisibility(false)
  mockedPreview.mockResolvedValue(preview())
  mockedHistory.mockResolvedValue({ history: [] })
  mockedOperation.mockResolvedValue(operation())
})

afterEach(() => {
  setVisibility(false)
  vi.useRealTimers()
})

describe('WaveformGenerationCard bulk polling', () => {
  it('polls an active operation resumed from history', async () => {
    await renderWithRunningOperation()
    await advance(1000)
    expect(mockedOperation).toHaveBeenCalledTimes(1)
    expect(mockedOperation.mock.calls[0]?.[0]).toBe('op-1')
    expect(mockedOperation.mock.calls[0]?.[1]).toBeInstanceOf(AbortSignal)
    await advance(1000)
    expect(mockedOperation).toHaveBeenCalledTimes(2)
  })

  it('backs off from 1 s to 2.5 s to 5 s as the run gets older', async () => {
    await renderWithRunningOperation()

    // Startup phase: 1 s cadence for the first ~15 s.
    await advance(14_000)
    const afterStartup = mockedOperation.mock.calls.length
    expect(afterStartup).toBeGreaterThanOrEqual(13)
    expect(afterStartup).toBeLessThanOrEqual(15)

    // Active phase: 2.5 s cadence up to 2 min (105 s window -> ~42 polls).
    await advance(120_000 - 14_000)
    const afterActive = mockedOperation.mock.calls.length
    expect(afterActive - afterStartup).toBeGreaterThanOrEqual(40)
    expect(afterActive - afterStartup).toBeLessThanOrEqual(44)

    // Steady state: 5 s cadence.
    await advance(25_000)
    const steady = mockedOperation.mock.calls.length - afterActive
    expect(steady).toBeGreaterThanOrEqual(4)
    expect(steady).toBeLessThanOrEqual(6)
  })

  it('stops polling and refreshes the preview on terminal success', async () => {
    await renderWithRunningOperation()
    await advance(1000)
    mockedOperation.mockResolvedValue(operation({ status: 'completed', processed: 10 }))
    const previewsBefore = mockedPreview.mock.calls.length

    await advance(1000)
    expect(mockedOperation).toHaveBeenCalledTimes(2)
    expect(mockedPreview.mock.calls.length).toBeGreaterThan(previewsBefore)

    await advance(30_000)
    expect(mockedOperation).toHaveBeenCalledTimes(2)
  })

  it('stops polling on terminal failure', async () => {
    await renderWithRunningOperation()
    await advance(1000)
    mockedOperation.mockResolvedValue(operation({ status: 'failed', error_reason: 'boom' }))
    await advance(1000)
    expect(mockedOperation).toHaveBeenCalledTimes(2)
    await advance(30_000)
    expect(mockedOperation).toHaveBeenCalledTimes(2)
  })

  it('stops polling once a cancelled operation reaches its terminal state', async () => {
    await renderWithRunningOperation()
    await advance(1000)
    // Backend keeps the row "running" with cancel_requested until the
    // in-flight track finishes, then flips to "cancelled".
    mockedOperation.mockResolvedValue(operation({ cancel_requested: true }))
    await advance(1000)
    mockedOperation.mockResolvedValue(operation({ status: 'cancelled', cancel_requested: true }))
    await advance(1000)
    expect(mockedOperation).toHaveBeenCalledTimes(3)
    await advance(30_000)
    expect(mockedOperation).toHaveBeenCalledTimes(3)
  })

  it('stops polling when a poll request fails', async () => {
    await renderWithRunningOperation()
    await advance(1000)
    mockedOperation.mockRejectedValue(new Error('network down'))
    await advance(1000)
    expect(mockedOperation).toHaveBeenCalledTimes(2)
    await advance(30_000)
    expect(mockedOperation).toHaveBeenCalledTimes(2)
  })

  it('cleans up timers on unmount', async () => {
    const view = await renderWithRunningOperation()
    await advance(2000)
    expect(mockedOperation).toHaveBeenCalledTimes(2)
    view.unmount()
    await advance(30_000)
    expect(mockedOperation).toHaveBeenCalledTimes(2)
  })

  it('keeps a single polling stream across a remount (no stacked timers)', async () => {
    const view = await renderWithRunningOperation()
    await advance(3000)
    expect(mockedOperation).toHaveBeenCalledTimes(3)
    view.unmount()

    await renderWithRunningOperation()
    mockedOperation.mockClear()
    await advance(3000)
    // Exactly one fetch per 1 s startup tick -- a second stacked stream
    // would double this count.
    expect(mockedOperation).toHaveBeenCalledTimes(3)
  })

  it('pauses polling while the tab is hidden and resumes immediately on return', async () => {
    await renderWithRunningOperation()
    await advance(1000)
    expect(mockedOperation).toHaveBeenCalledTimes(1)

    setVisibility(true)
    await advance(10_000)
    expect(mockedOperation).toHaveBeenCalledTimes(1)

    // Becoming visible triggers an immediate poll without waiting for the
    // next scheduled tick, so a terminal state reached while hidden is
    // picked up right away.
    setVisibility(false)
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(mockedOperation).toHaveBeenCalledTimes(2)

    await advance(1000)
    expect(mockedOperation).toHaveBeenCalledTimes(3)
  })

  it('picks up a terminal state reached while the tab was hidden', async () => {
    await renderWithRunningOperation()
    await advance(1000)
    mockedOperation.mockResolvedValue(operation({ status: 'completed', processed: 10 }))

    setVisibility(true)
    await advance(10_000)
    expect(mockedOperation).toHaveBeenCalledTimes(1)

    setVisibility(false)
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(mockedOperation).toHaveBeenCalledTimes(2)
    await advance(30_000)
    expect(mockedOperation).toHaveBeenCalledTimes(2)
  })

  it('does not overlap requests or fork the timeout chain when visibility changes mid-request', async () => {
    const pending = deferred<WaveformBulkOperation>()
    mockedOperation.mockImplementationOnce(() => pending.promise)
    await renderWithRunningOperation()

    // The first timeout has fired, so its ID must no longer represent a
    // scheduled timer while the request remains unresolved.
    await advance(1000)
    expect(mockedOperation).toHaveBeenCalledTimes(1)

    setVisibility(true)
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    setVisibility(false)
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })

    // Returning visible during an in-flight request must neither issue a
    // second request nor start a competing timer chain.
    expect(mockedOperation).toHaveBeenCalledTimes(1)
    await advance(5000)
    expect(mockedOperation).toHaveBeenCalledTimes(1)

    await act(async () => {
      pending.resolve(operation({ processed: 1, generated: 1 }))
      await pending.promise
    })
    await advance(999)
    expect(mockedOperation).toHaveBeenCalledTimes(1)
    await advance(1)
    expect(mockedOperation).toHaveBeenCalledTimes(2)
    await advance(1000)
    expect(mockedOperation).toHaveBeenCalledTimes(3)
  })

  it('aborts an in-flight poll and ignores its stale completion after unmount', async () => {
    const pending = deferred<WaveformBulkOperation>()
    let pollSignal: AbortSignal | undefined
    mockedOperation.mockImplementationOnce((_operationId, signal) => {
      pollSignal = signal
      return pending.promise
    })
    const view = await renderWithRunningOperation()
    await advance(1000)
    expect(mockedOperation).toHaveBeenCalledTimes(1)
    expect(pollSignal?.aborted).toBe(false)
    const previewsBeforeUnmount = mockedPreview.mock.calls.length

    view.unmount()
    expect(pollSignal?.aborted).toBe(true)
    await act(async () => {
      pending.resolve(operation({ status: 'completed', processed: 10 }))
      await pending.promise
    })
    await advance(30_000)

    expect(mockedOperation).toHaveBeenCalledTimes(1)
    expect(mockedPreview).toHaveBeenCalledTimes(previewsBeforeUnmount)
  })

  it('does not let an older status request overwrite a terminal cancel response', async () => {
    const pendingPoll = deferred<WaveformBulkOperation>()
    let pollSignal: AbortSignal | undefined
    mockedOperation.mockImplementationOnce((_operationId, signal) => {
      pollSignal = signal
      return pendingPoll.promise
    })
    mockedCancel.mockResolvedValue(operation({ status: 'cancelled', cancel_requested: true }))
    await renderWithRunningOperation()
    await advance(1000)

    await act(async () => {
      screen.getByRole('button', { name: 'Cancel' }).click()
    })
    await flush()
    expect(pollSignal?.aborted).toBe(true)
    expect(screen.getByText('Cancelled')).toBeInTheDocument()

    await act(async () => {
      pendingPoll.resolve(operation())
      await pendingPoll.promise
    })
    await advance(30_000)

    expect(screen.getByText('Cancelled')).toBeInTheDocument()
    expect(mockedOperation).toHaveBeenCalledTimes(1)
  })

  it('resumes polling if a cancel request fails', async () => {
    mockedCancel.mockRejectedValue(new Error('network down'))
    await renderWithRunningOperation()

    await act(async () => {
      screen.getByRole('button', { name: 'Cancel' }).click()
    })
    await flush()
    await advance(1000)

    expect(mockedOperation).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Could not reach waveform generation.')).toBeInTheDocument()
  })

  it('renders progress updates from poll responses', async () => {
    await renderWithRunningOperation()
    await advance(1000)
    expect(screen.getByText('0 / 10')).toBeTruthy()

    mockedOperation.mockResolvedValue(operation({ processed: 4, generated: 4 }))
    await advance(1000)
    expect(screen.getByText('4 / 10')).toBeTruthy()
    expect(screen.getByText('4')).toBeTruthy()
  })

  it('does not poll when there is no running operation', async () => {
    render(<WaveformGenerationCard />)
    await flush()
    await advance(30_000)
    expect(mockedOperation).not.toHaveBeenCalled()
  })

  it('starts polling after an explicit start and polls the new operation', async () => {
    render(<WaveformGenerationCard />)
    await flush()
    mockedStart.mockResolvedValue({ id: 'op-9', total_tracks: 10, eligible_total: 10 })
    mockedOperation.mockResolvedValue(operation({ id: 'op-9' }))
    mockedCancel.mockResolvedValue(operation({ id: 'op-9', status: 'cancelled' }))

    await act(async () => {
      screen.getByRole('button', { name: /generate missing waveforms/i }).click()
    })
    await flush()
    // start() does one immediate fetch, then the poller takes over.
    expect(mockedOperation).toHaveBeenCalledWith('op-9')
    const afterStart = mockedOperation.mock.calls.length
    await advance(2000)
    expect(mockedOperation.mock.calls.length).toBe(afterStart + 2)
  })

  it('ignores mount history that resolves after a new operation starts', async () => {
    const pendingHistory = deferred<{ history: WaveformBulkOperation[] }>()
    mockedHistory.mockReturnValue(pendingHistory.promise)
    mockedStart.mockResolvedValue({ id: 'op-9', total_tracks: 10, eligible_total: 10 })
    mockedOperation.mockResolvedValue(operation({ id: 'op-9' }))
    render(<WaveformGenerationCard />)
    await flush()

    await act(async () => {
      screen.getByRole('button', { name: /generate missing waveforms/i }).click()
    })
    await flush()
    expect(mockedOperation.mock.calls.map(([operationId]) => operationId)).toEqual(['op-9'])

    await act(async () => {
      pendingHistory.resolve({ history: [operation({ id: 'op-old' })] })
      await pendingHistory.promise
    })
    await advance(1000)

    expect(mockedOperation.mock.calls.map(([operationId]) => operationId)).toEqual(['op-9', 'op-9'])
  })
})
