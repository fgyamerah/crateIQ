import { useCallback, useEffect, useRef, useState } from 'react'
import { AudioWaveform, Loader2, RefreshCw, XCircle } from 'lucide-react'
import { ApiError } from '../../api/client'
import {
  cancelWaveformBulkOperation,
  fetchWaveformBulkHistory,
  fetchWaveformBulkOperation,
  fetchWaveformBulkPreview,
  startWaveformBulkGenerate,
} from '../../api/waveformBulk'
import type {
  WaveformBulkOperation,
  WaveformBulkOperationStatus,
  WaveformBulkPreview,
} from '../../types/waveformBulk'
import Badge, { type BadgeTone } from '../ui/Badge'
import KpiCard from '../ui/KpiCard'
import StatusStrip from '../ui/StatusStrip'

/** Adaptive bulk-operation polling cadence: quick while a run spins up,
 * slower steady-state for long runs, so a 10+ minute generation does not
 * hammer the status route (~500 requests over 13 min at a flat 2 s).
 * Progress rendering, terminal-state stops, and cancel semantics are
 * unchanged -- only the timer cadence moved. */
const POLL_FAST_INTERVAL_MS = 1000
const POLL_FAST_UNTIL_MS = 15_000
const POLL_MEDIUM_INTERVAL_MS = 2500
const POLL_MEDIUM_UNTIL_MS = 120_000
const POLL_STEADY_INTERVAL_MS = 5000

const STATUS_TONE: Record<WaveformBulkOperationStatus, BadgeTone> = {
  running: 'running',
  completed: 'succeeded',
  failed: 'failed',
  cancelled: 'cancelled',
}

const STATUS_LABEL: Record<WaveformBulkOperationStatus, string> = {
  running: 'Generating…',
  completed: 'Complete',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.displayMessage : 'Could not reach waveform generation.'
}

/** The run finishing without crashing is a different fact from every track
 * inside it succeeding -- a plain green "Complete" on a run where half the
 * tracks failed would visually collapse a degraded result into something
 * that looks fine. Distinguish it without changing the label. */
function statusTone(operation: WaveformBulkOperation): BadgeTone {
  if (operation.status === 'completed' && operation.failed > 0) return 'pending'
  return STATUS_TONE[operation.status]
}

/** A truthful, non-technical reason for a terminal state. Never guesses. */
function reasonLabel(operation: WaveformBulkOperation): string | null {
  if (operation.status === 'cancelled') return 'Cancelled by user request.'
  if (operation.status === 'failed') {
    if (operation.error_reason === 'backend_restarted') {
      return 'The backend restarted while this run was in progress. Already-generated waveforms were kept.'
    }
    return operation.error_reason ? `Could not start: ${operation.error_reason}` : 'This run failed.'
  }
  return null
}

/**
 * Jobs page card for bulk waveform generation. Reuses the existing Jobs/
 * Analysis visual patterns (KpiCard, job-progress bar, Badge) rather than
 * introducing a parallel job UI. Generation is always explicit: nothing
 * here starts a run on mount -- only picks up and displays a run already in
 * progress from persisted history, so a page reload mid-run still shows
 * real state instead of losing it.
 */
export default function WaveformGenerationCard() {
  const [preview, setPreview] = useState<WaveformBulkPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(true)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [operation, setOperation] = useState<WaveformBulkOperation | null>(null)
  const [starting, setStarting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollAbortRef = useRef<AbortController | null>(null)
  const pollSessionRef = useRef(0)
  const visibilityRef = useRef<(() => void) | null>(null)
  const mountedRef = useRef(true)
  const previewRequestRef = useRef(0)
  const historyRequestRef = useRef(0)

  const loadPreview = useCallback(async () => {
    if (!mountedRef.current) return
    const requestId = ++previewRequestRef.current
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      const updated = await fetchWaveformBulkPreview()
      if (mountedRef.current && previewRequestRef.current === requestId) {
        setPreview(updated)
      }
    } catch (err) {
      if (mountedRef.current && previewRequestRef.current === requestId) {
        setPreviewError(errorMessage(err))
      }
    } finally {
      if (mountedRef.current && previewRequestRef.current === requestId) {
        setPreviewLoading(false)
      }
    }
  }, [])

  const stopPolling = useCallback(() => {
    // Invalidating the session first makes every awaiting callback stale before
    // any timer/listener/request cleanup can itself trigger a continuation.
    pollSessionRef.current += 1
    if (pollRef.current !== null) {
      clearTimeout(pollRef.current)
      pollRef.current = null
    }
    if (pollAbortRef.current !== null) {
      pollAbortRef.current.abort()
      pollAbortRef.current = null
    }
    if (visibilityRef.current !== null) {
      document.removeEventListener('visibilitychange', visibilityRef.current)
      visibilityRef.current = null
    }
  }, [])

  const pollOperation = useCallback((operationId: string) => {
    stopPolling()
    if (!mountedRef.current) return
    const session = pollSessionRef.current
    const startedAt = Date.now()
    const nextDelay = () => {
      const elapsed = Date.now() - startedAt
      if (elapsed < POLL_FAST_UNTIL_MS) return POLL_FAST_INTERVAL_MS
      if (elapsed < POLL_MEDIUM_UNTIL_MS) return POLL_MEDIUM_INTERVAL_MS
      return POLL_STEADY_INTERVAL_MS
    }
    const isActive = () => mountedRef.current && pollSessionRef.current === session

    // pollRef owns only a timer that has not fired yet. Clearing it at callback
    // entry prevents visibility changes from mistaking a stale timer ID for an
    // idle polling chain. The non-null check is the single-timer invariant.
    const scheduleNext = () => {
      if (!isActive() || pollRef.current !== null || pollAbortRef.current !== null) return
      const timeoutId = setTimeout(() => {
        if (pollRef.current !== timeoutId) return
        pollRef.current = null
        void tick()
      }, nextDelay())
      pollRef.current = timeoutId
    }

    // Chained timeouts plus one AbortController-owned request slot guarantee
    // that a slow response, a visibility event, or a superseded session cannot
    // create overlapping requests or a second timeout chain.
    const tick = async () => {
      if (!isActive()) return
      // No network traffic while the tab is hidden; the visibility listener
      // fires an immediate tick on return, so a terminal state is picked up
      // as soon as the user is back instead of being missed.
      if (document.hidden) {
        scheduleNext()
        return
      }
      // A visibility transition can ask for an immediate tick while the prior
      // request is still awaiting. That request owns the next scheduling step.
      if (pollAbortRef.current !== null) return

      const controller = new AbortController()
      pollAbortRef.current = controller
      try {
        const updated = await fetchWaveformBulkOperation(operationId, controller.signal)
        if (!isActive()) return
        setOperation(updated)
        if (updated.status !== 'running') {
          stopPolling()
          void loadPreview()
          return
        }
      } catch {
        if (isActive()) stopPolling()
        return
      } finally {
        if (pollAbortRef.current === controller) {
          pollAbortRef.current = null
        }
      }
      scheduleNext()
    }
    const onVisibilityChange = () => {
      if (document.visibilityState !== 'visible' || !isActive()) return
      if (pollRef.current !== null) clearTimeout(pollRef.current)
      pollRef.current = null
      // If a request is already in flight, its completion owns the next tick;
      // otherwise resume immediately instead of waiting out the hidden delay.
      if (pollAbortRef.current === null) void tick()
    }
    visibilityRef.current = onVisibilityChange
    document.addEventListener('visibilitychange', onVisibilityChange)
    scheduleNext()
  }, [loadPreview, stopPolling])

  useEffect(() => {
    let active = true
    mountedRef.current = true
    const historyRequestId = ++historyRequestRef.current
    void loadPreview()
    // Recover a run already in progress (e.g. this page mounted after a
    // reload mid-run) from persisted history -- this only reads state, it
    // never starts anything.
    fetchWaveformBulkHistory()
      .then((result) => {
        if (!active || historyRequestRef.current !== historyRequestId) return
        const latest = result.history[0]
        if (!latest) return
        setOperation(latest)
        if (latest.status === 'running') pollOperation(latest.id)
      })
      .catch(() => {})
    return () => {
      active = false
      mountedRef.current = false
      previewRequestRef.current += 1
      historyRequestRef.current += 1
      stopPolling()
    }
    // Mount-only: re-running this on every loadPreview/pollOperation
    // identity change would refetch history in a loop for no reason.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const start = async () => {
    setStarting(true)
    setActionError(null)
    try {
      const started = await startWaveformBulkGenerate()
      if (!mountedRef.current) return
      // A late mount-time history response describes state from before this
      // explicit run and must never supersede it or restart an older poller.
      historyRequestRef.current += 1
      stopPolling()
      const initial = await fetchWaveformBulkOperation(started.id)
      if (!mountedRef.current) return
      setOperation(initial)
      if (initial.status === 'running') pollOperation(initial.id)
      else void loadPreview()
    } catch (err) {
      if (mountedRef.current) setActionError(errorMessage(err))
    } finally {
      if (mountedRef.current) setStarting(false)
    }
  }

  const cancel = async () => {
    if (!operation) return
    const current = operation
    setCancelling(true)
    setActionError(null)
    // The cancel response is newer authority than any status read already in
    // flight. Invalidate that read so it cannot overwrite cancel_requested or
    // a terminal cancellation with stale running state.
    stopPolling()
    try {
      const updated = await cancelWaveformBulkOperation(current.id)
      if (!mountedRef.current) return
      setOperation(updated)
      if (updated.status === 'running') pollOperation(updated.id)
      else void loadPreview()
    } catch (err) {
      if (mountedRef.current) {
        setActionError(errorMessage(err))
        pollOperation(current.id)
      }
    } finally {
      if (mountedRef.current) setCancelling(false)
    }
  }

  const running = operation?.status === 'running'
  const reason = operation ? reasonLabel(operation) : null
  const canStart = Boolean(preview) && (preview?.eligible_to_generate ?? 0) > 0 && !running

  return (
    <section className="analysis-job-history" aria-label="Waveform generation">
      <div className="analysis-history-head">
        <div>
          <h2 className="card-title">Waveform generation</h2>
          <p className="muted">
            Generate cached waveform previews for tracks that do not have one yet.
            Source audio, tags, BPM, key, and cue points are never touched.
          </p>
        </div>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => void loadPreview()}
          disabled={previewLoading}
        >
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {previewError && <StatusStrip tone="danger">{previewError}</StatusStrip>}

      {previewLoading && !preview ? (
        <p className="muted">Loading waveform status…</p>
      ) : preview && (
        <div className="analysis-jobs-kpis">
          <KpiCard tone="emerald" label="Ready" value={preview.ready} sub={`${preview.total_tracks} tracks total`} />
          <KpiCard tone="muted" label="Missing" value={preview.missing} sub="No waveform yet" />
          <KpiCard tone="cyan" label="Generating" value={preview.generating} sub="Queued or in progress" />
          <KpiCard
            tone="coral"
            label="Failed"
            value={preview.failed}
            sub={
              preview.unsupported
                ? preview.failed > 0
                  ? `${preview.unsupported} unsupported · ${preview.failed} can retry`
                  : `${preview.unsupported} unsupported format`
                : 'Can be retried'
            }
          />
        </div>
      )}

      {!running && (
        <div className="settings-action-row">
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={() => void start()}
            disabled={starting || !canStart}
          >
            <AudioWaveform size={13} />
            {starting ? 'Starting…' : 'Generate missing waveforms'}
          </button>
          {preview && preview.eligible_to_generate === 0 && (
            <span className="analysis-job-run-hint">Every track already has a waveform, or one is in progress.</span>
          )}
        </div>
      )}

      {actionError && <StatusStrip tone="danger">{actionError}</StatusStrip>}

      {operation && (
        <div className="analysis-job-preview" aria-live="polite">
          <div className="settings-import-result-head">
            <div>
              <h3 className="card-title">{running ? 'Generating waveforms' : 'Last run'}</h3>
              <p className="muted">{operation.eligible_total} tracks were eligible when this run started.</p>
            </div>
            <Badge tone={statusTone(operation)}>{STATUS_LABEL[operation.status]}</Badge>
          </div>
          {running && operation.eligible_total > 0 && (
            <div className="job-progress">
              <div className="job-progress-bar">
                <div
                  className="job-progress-fill"
                  style={{ width: `${Math.min(100, Math.round((operation.processed / operation.eligible_total) * 100))}%` }}
                />
              </div>
              <span className="job-progress-pct">{operation.processed} / {operation.eligible_total}</span>
            </div>
          )}
          <div className="settings-import-summary">
            <span><strong>{operation.generated}</strong> generated</span>
            <span><strong>{operation.skipped}</strong> skipped</span>
            <span><strong>{operation.failed}</strong> failed</span>
            {operation.remaining_missing !== null && (
              <span><strong>{operation.remaining_missing}</strong> remaining</span>
            )}
          </div>
          {reason && <StatusStrip tone={operation.status === 'cancelled' ? 'warn' : 'danger'}>{reason}</StatusStrip>}
          {running && (
            <button
              type="button"
              className="btn btn--danger btn--sm"
              disabled={cancelling || operation.cancel_requested}
              onClick={() => void cancel()}
            >
              {cancelling ? <Loader2 size={13} className="spin" /> : <XCircle size={13} />}
              {operation.cancel_requested ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
        </div>
      )}
    </section>
  )
}
