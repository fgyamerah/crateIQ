import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, XCircle, Loader2 } from 'lucide-react'
import { ApiError } from '../../api/client'
import { cancelAnalysisOperation, fetchAnalysisJobHistory, fetchAnalysisOperation } from '../../api/analysis'
import type { AnalysisOperation, AnalysisOperationStatus } from '../../types/analysis'
import Badge, { type BadgeTone } from '../ui/Badge'
import EmptyState from '../ui/EmptyState'
import StatusStrip from '../ui/StatusStrip'

const STATUS_TONE: Record<AnalysisOperationStatus, BadgeTone> = {
  running: 'running',
  completed: 'succeeded',
  failed: 'failed',
  cancelled: 'cancelled',
}

const STATUS_LABEL: Record<AnalysisOperationStatus, string> = {
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

/** The job finishing without crashing is a different fact from every track
 * inside it succeeding -- a plain green "Completed" on a run where nothing
 * actually succeeded would visually collapse a degraded result into
 * something that looks fine. Distinguish it without changing the label. */
function statusTone(operation: AnalysisOperation): BadgeTone {
  if (operation.status === 'completed' && operation.failed > 0) return 'pending'
  return STATUS_TONE[operation.status]
}

const JOB_TYPE_LABEL: Record<string, string> = {
  bpm_analysis: 'BPM analysis',
  key_analysis: 'Key/Camelot analysis',
}

function jobTypeLabel(jobType: string): string {
  return JOB_TYPE_LABEL[jobType] ?? jobType
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function formatDuration(startedAt: string | null, finishedAt: string | null): string {
  if (!startedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now()
  const sec = Math.max(0, Math.floor((end - start) / 1000))
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m ${sec % 60}s`
}

/** A truthful, non-technical reason for a terminal state. Never guesses. */
function reasonLabel(operation: AnalysisOperation): string | null {
  if (operation.status === 'cancelled') return 'Cancelled by user request.'
  if (operation.status === 'failed') {
    if (operation.error_reason === 'backend_restarted') return 'The backend restarted while this run was in progress.'
    return operation.error_reason ? `Failed: ${operation.error_reason}` : 'Failed.'
  }
  return null
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.displayMessage : 'Could not load analysis history.'
}

function OperationDetail({
  operation,
  onCancelled,
}: {
  operation: AnalysisOperation
  onCancelled: (updated: AnalysisOperation) => void
}) {
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const reason = reasonLabel(operation)
  const showBar = operation.status === 'running' && operation.considered > 0

  const cancel = async () => {
    setCancelling(true)
    setCancelError(null)
    try {
      onCancelled(await cancelAnalysisOperation(operation.id))
    } catch (err) {
      setCancelError(errorMessage(err))
    } finally {
      setCancelling(false)
    }
  }

  return (
    <>
      <div className="analysis-history-detail-head">
        <h3>{jobTypeLabel(operation.job_type)}</h3>
        <Badge tone={statusTone(operation)}>{STATUS_LABEL[operation.status]}</Badge>
      </div>
      {showBar && (
        <div className="job-progress">
          <div className="job-progress-bar">
            <div
              className="job-progress-fill"
              style={{ width: `${Math.min(100, Math.round((operation.processed / operation.considered) * 100))}%` }}
            />
          </div>
          <span className="job-progress-pct">{operation.processed} of {operation.considered}</span>
        </div>
      )}
      <dl className="lib-defs">
        <dt>Mode</dt><dd>{operation.mode}</dd>
        <dt>Started</dt><dd>{formatDateTime(operation.started_at)}</dd>
        <dt>Finished</dt><dd>{formatDateTime(operation.finished_at)}</dd>
        <dt>Scope limit</dt><dd>{operation.scope_limit}</dd>
        <dt>Eligible in library</dt><dd>{operation.eligible_total}</dd>
        <dt>Considered this run</dt><dd>{operation.considered}</dd>
        <dt>Succeeded</dt><dd>{operation.succeeded}</dd>
        <dt>Skipped</dt><dd>{operation.skipped}</dd>
        <dt>Failed</dt><dd>{operation.failed}</dd>
        <dt>Remaining missing</dt><dd>{operation.remaining_missing ?? '—'}</dd>
      </dl>
      {reason && <StatusStrip tone={operation.status === 'cancelled' ? 'warn' : 'danger'}>{reason}</StatusStrip>}
      {operation.warnings.length > 0 && (
        <div className="analysis-history-warnings">
          <strong>Warnings</strong>
          <ul>{operation.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
        </div>
      )}
      {operation.status === 'running' && (
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
      {cancelError && <StatusStrip tone="danger">{cancelError}</StatusStrip>}
    </>
  )
}

interface Props {
  /** Bump this from the parent after a confirmed run completes to refresh immediately. */
  refreshSignal: number
}

/**
 * Persisted history of explicit, confirmed BPM/key analysis runs.
 * Candidate previews are never shown here -- only runs that actually began
 * work. No fabricated progress: the bar only appears once real
 * processed/considered counts exist, and reflects them directly.
 */
export default function AnalysisOperationsHistory({ refreshSignal }: Props) {
  const [operations, setOperations] = useState<AnalysisOperation[]>([])
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<AnalysisOperation | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchAnalysisJobHistory()
      setOperations(result.history)
      setMessage(result.message)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load, refreshSignal])

  useEffect(() => {
    if (!selectedId) { setDetail(null); return }
    let cancelled = false
    fetchAnalysisOperation(selectedId)
      .then((result) => { if (!cancelled) setDetail(result) })
      .catch(() => { if (!cancelled) setDetail(null) })
    return () => { cancelled = true }
  }, [selectedId])

  // Poll while any operation is running so status/progress catch up without
  // requiring a manual refresh -- still real backend state, just re-fetched.
  useEffect(() => {
    if (!operations.some((operation) => operation.status === 'running')) return
    const timer = window.setInterval(() => {
      void load()
      if (selectedId) fetchAnalysisOperation(selectedId).then(setDetail).catch(() => {})
    }, 2000)
    return () => window.clearInterval(timer)
  }, [operations, load, selectedId])

  return (
    <section className="analysis-job-history">
      <div className="analysis-history-head">
        <div>
          <h2 className="card-title">Analysis history</h2>
          <p className="muted">{message || 'Explicit, confirmed BPM/key runs only. Candidate previews are never recorded.'}</p>
        </div>
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={13} /> Refresh
        </button>
      </div>
      {error && <StatusStrip tone="danger">{error}</StatusStrip>}
      {loading ? (
        <p className="muted">Loading analysis history…</p>
      ) : operations.length === 0 ? (
        <EmptyState title="No confirmed runs yet" message={message || 'Run BPM or key analysis above, then confirm, to see it here.'} />
      ) : (
        <div className="analysis-history-layout">
          <div className="table-wrapper">
            <table className="table table--jobs">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Scope</th>
                  <th>Result</th>
                  <th className="nowrap">Started</th>
                  <th className="nowrap">Duration</th>
                </tr>
              </thead>
              <tbody>
                {operations.map((operation) => (
                  <tr
                    key={operation.id}
                    className={[
                      'row--clickable',
                      operation.id === selectedId ? 'is-selected' : '',
                      operation.status === 'running' ? 'row--running' : '',
                      operation.status === 'failed' ? 'row--failed' : '',
                      operation.status === 'cancelled' ? 'row--cancelled' : '',
                    ].filter(Boolean).join(' ')}
                    tabIndex={0}
                    aria-selected={operation.id === selectedId}
                    onClick={() => setSelectedId(operation.id)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        setSelectedId(operation.id)
                      }
                    }}
                  >
                    <td>{jobTypeLabel(operation.job_type)}</td>
                    <td><Badge tone={statusTone(operation)}>{STATUS_LABEL[operation.status]}</Badge></td>
                    <td className="muted">{operation.considered} of {operation.eligible_total}</td>
                    <td className="muted">{operation.succeeded} ok · {operation.skipped} skip · {operation.failed} fail</td>
                    <td className="muted nowrap td-timestamp">{formatDateTime(operation.started_at)}</td>
                    <td className="muted td-duration">{formatDuration(operation.started_at, operation.finished_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <aside className="analysis-history-detail card" aria-live="polite">
            {!selectedId ? (
              <EmptyState title="Select a run" message="Choose a row to see its full detail." />
            ) : detail && detail.id === selectedId ? (
              <OperationDetail
                operation={detail}
                onCancelled={(updated) => { setDetail(updated); void load() }}
              />
            ) : (
              <p className="muted">Loading…</p>
            )}
          </aside>
        </div>
      )}
    </section>
  )
}
