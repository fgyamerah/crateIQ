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

const POLL_INTERVAL_MS = 2000

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
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadPreview = useCallback(async () => {
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      setPreview(await fetchWaveformBulkPreview())
    } catch (err) {
      setPreviewError(errorMessage(err))
    } finally {
      setPreviewLoading(false)
    }
  }, [])

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const pollOperation = useCallback((operationId: string) => {
    stopPolling()
    pollRef.current = setInterval(() => {
      fetchWaveformBulkOperation(operationId)
        .then((updated) => {
          setOperation(updated)
          if (updated.status !== 'running') {
            stopPolling()
            void loadPreview()
          }
        })
        .catch(() => stopPolling())
    }, POLL_INTERVAL_MS)
  }, [loadPreview, stopPolling])

  useEffect(() => {
    void loadPreview()
    // Recover a run already in progress (e.g. this page mounted after a
    // reload mid-run) from persisted history -- this only reads state, it
    // never starts anything.
    fetchWaveformBulkHistory()
      .then((result) => {
        const latest = result.history[0]
        if (!latest) return
        setOperation(latest)
        if (latest.status === 'running') pollOperation(latest.id)
      })
      .catch(() => {})
    return () => stopPolling()
    // Mount-only: re-running this on every loadPreview/pollOperation
    // identity change would refetch history in a loop for no reason.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const start = async () => {
    setStarting(true)
    setActionError(null)
    try {
      const started = await startWaveformBulkGenerate()
      const initial = await fetchWaveformBulkOperation(started.id)
      setOperation(initial)
      if (initial.status === 'running') pollOperation(initial.id)
      else void loadPreview()
    } catch (err) {
      setActionError(errorMessage(err))
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    if (!operation) return
    setCancelling(true)
    setActionError(null)
    try {
      setOperation(await cancelWaveformBulkOperation(operation.id))
    } catch (err) {
      setActionError(errorMessage(err))
    } finally {
      setCancelling(false)
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
