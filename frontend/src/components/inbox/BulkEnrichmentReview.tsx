import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, ChevronRight, Loader2, ShieldCheck } from 'lucide-react'
import { ApiError } from '../../api/client'
import {
  acceptSafeEnrichmentSuggestions,
  fetchBulkEnrichmentSummary,
  keepCurrentBulkEnrichment,
} from '../../api/enrichmentReview'
import type { BulkEnrichmentActionResult, BulkEnrichmentSummary } from '../../types/enrichmentReview'
import Badge from '../ui/Badge'
import StatusStrip from '../ui/StatusStrip'

interface Props {
  trackIds: number[]
  refreshKey: number
  onReviewExceptions: (trackIds: number[]) => void
  onResolved: (summary: BulkEnrichmentSummary) => Promise<void> | void
  onClose: () => void
}

const messageFor = (error: unknown) => error instanceof ApiError
  ? error.displayMessage
  : error instanceof Error ? error.message : 'Could not update bulk enrichment review.'

const stateLabel = (state: string) => state === 'safe' ? 'Safe' : state === 'exception' ? 'Review' : 'No suggestion'
const stateTone = (state: string): 'succeeded' | 'failed' | 'pending' => state === 'safe' ? 'succeeded' : state === 'exception' ? 'failed' : 'pending'

export default function BulkEnrichmentReview({ trackIds, refreshKey, onReviewExceptions, onResolved, onClose }: Props) {
  const [summary, setSummary] = useState<BulkEnrichmentSummary | null>(null)
  const [busy, setBusy] = useState<'load' | 'accept' | 'keep' | null>('load')
  const [confirming, setConfirming] = useState<'accept' | 'keep' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [messageTone, setMessageTone] = useState<'good' | 'warn'>('good')
  const [actionResult, setActionResult] = useState<BulkEnrichmentActionResult | null>(null)
  const loadRequestRef = useRef(0)
  const confirmButtonRef = useRef<HTMLButtonElement>(null)
  const actionTriggerRef = useRef<HTMLButtonElement | null>(null)
  const outcomeRef = useRef<HTMLDivElement>(null)
  const selectionKey = trackIds.join(',')
  const selectionKeyRef = useRef(selectionKey)
  selectionKeyRef.current = selectionKey

  const load = useCallback(async () => {
    const requestId = ++loadRequestRef.current
    setBusy('load')
    setError(null)
    try {
      const next = await fetchBulkEnrichmentSummary(trackIds)
      if (requestId === loadRequestRef.current) setSummary(next)
    } catch (loadError) {
      if (requestId === loadRequestRef.current) setError(messageFor(loadError))
    } finally {
      if (requestId === loadRequestRef.current) setBusy(null)
    }
  }, [trackIds])

  useEffect(() => {
    setSummary(null)
    setConfirming(null)
    setMessage(null)
    setActionResult(null)
  }, [selectionKey])
  useEffect(() => {
    void load()
    return () => { loadRequestRef.current += 1 }
  }, [load, refreshKey])
  useEffect(() => { if (confirming) confirmButtonRef.current?.focus() }, [confirming])
  useEffect(() => { if (message) outcomeRef.current?.focus() }, [message])

  function openConfirmation(action: 'accept' | 'keep', trigger: HTMLButtonElement) {
    actionTriggerRef.current = trigger
    setConfirming(action)
  }

  function cancelConfirmation() {
    setConfirming(null)
    window.setTimeout(() => actionTriggerRef.current?.focus(), 0)
  }

  async function acceptSafe() {
    const actionSelectionKey = selectionKey
    setBusy('accept')
    setError(null)
    try {
      const result = await acceptSafeEnrichmentSuggestions(trackIds)
      if (selectionKeyRef.current !== actionSelectionKey) return
      setSummary(result.summary)
      setActionResult(result)
      const problems = (result.skipped ?? 0) + (result.failed ?? 0)
      setMessageTone(problems ? 'warn' : 'good')
      setMessage(
        `${result.applied ?? 0} safe suggestion${result.applied === 1 ? '' : 's'} accepted` +
        `${result.skipped ? ` · ${result.skipped} skipped` : ''}` +
        `${result.failed ? ` · ${result.failed} failed` : ''}. Conflicts stayed unresolved.`,
      )
      setConfirming(null)
      await onResolved(result.summary)
    } catch (actionError) {
      if (selectionKeyRef.current === actionSelectionKey) setError(messageFor(actionError))
    } finally {
      if (selectionKeyRef.current === actionSelectionKey) setBusy(null)
    }
  }

  async function keepCurrent() {
    const actionSelectionKey = selectionKey
    setBusy('keep')
    setError(null)
    try {
      const result = await keepCurrentBulkEnrichment(trackIds)
      if (selectionKeyRef.current !== actionSelectionKey) return
      setSummary(result.summary)
      setActionResult(null)
      setMessageTone('good')
      setMessage(`Kept current metadata for ${result.kept_track_count ?? 0} track${result.kept_track_count === 1 ? '' : 's'}.`)
      setConfirming(null)
      await onResolved(result.summary)
    } catch (actionError) {
      if (selectionKeyRef.current === actionSelectionKey) setError(messageFor(actionError))
    } finally {
      if (selectionKeyRef.current === actionSelectionKey) setBusy(null)
    }
  }

  const exceptionIds = summary?.rows.filter((row) => row.review_state === 'exception').map((row) => row.track_id) ?? []

  return (
    <section className="card settings-card inbox-bulk-review" aria-labelledby="bulk-review-title">
      <div className="inbox-bulk-panel-head">
        <div>
          <h2 className="card-title" id="bulk-review-title"><ShieldCheck size={16} /> Bulk enrichment review</h2>
          <p className="muted">Selected: {trackIds.length} track{trackIds.length === 1 ? '' : 's'}</p>
        </div>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>Close</button>
      </div>

      {busy === 'load' && !summary && <p className="muted"><Loader2 size={13} className="spin" /> Classifying selected suggestions…</p>}
      {error && <StatusStrip tone="danger" onDismiss={() => setError(null)}>{error}</StatusStrip>}
      {message && <div ref={outcomeRef} tabIndex={-1} aria-label="Bulk enrichment result"><StatusStrip tone={messageTone} onDismiss={() => setMessage(null)}>{message}</StatusStrip></div>}
      {actionResult && (
        (actionResult.results?.some((item) => item.status === 'failed' || item.status === 'skipped') || actionResult.warnings?.length)
      ) && (
        <ul className="inbox-bulk-result-list" aria-label="Enrichment suggestions requiring attention">
          {actionResult.results?.filter((item) => item.status === 'failed' || item.status === 'skipped').map((item) => (
            <li key={`${item.track_id}-${item.suggestion_id}`}>
              <strong>Track {item.track_id}</strong>
              <span>{item.status}</span>
              <small>{item.reason || 'The suggestion was not applied.'}</small>
            </li>
          ))}
          {actionResult.warnings?.map((warning, index) => (
            <li key={`warning-${index}`}><strong>Warning</strong><span>attention</span><small>{warning}</small></li>
          ))}
        </ul>
      )}

      {summary && (
        <>
          <div className="inbox-bulk-review-counts" aria-label="Bulk enrichment summary">
            <strong>{summary.selected_count} selected</strong>
            <span><b>{summary.safe_count}</b> safe to accept</span>
            <span><b>{summary.exception_count}</b> need review</span>
            <span><b>{summary.no_suggestion_count}</b> no useful suggestion</span>
          </div>
          <p className="muted inbox-bulk-review-rule">{summary.message}</p>
          <div className="settings-actions inbox-bulk-primary-actions">
            <button type="button" className="btn btn--primary btn--sm" disabled={!summary.safe_count || busy !== null || confirming !== null} onClick={(event) => openConfirmation('accept', event.currentTarget)}>
              <Check size={13} /> Accept {summary.safe_count} Safe Suggestion{summary.safe_count === 1 ? '' : 's'}
            </button>
            <button type="button" className="btn btn--ghost btn--sm" disabled={busy !== null || confirming !== null || !summary.rows.some((row) => row.suggestion_count)} onClick={(event) => openConfirmation('keep', event.currentTarget)}>
              Keep Current for {summary.selected_count}
            </button>
            <button type="button" className="btn btn--ghost btn--sm" disabled={!summary.exception_count || busy !== null || confirming !== null} onClick={() => onReviewExceptions(exceptionIds)}>
              Review {summary.exception_count} Exception{summary.exception_count === 1 ? '' : 's'} <ChevronRight size={13} />
            </button>
          </div>

          {confirming && (
            <div className="inbox-bulk-confirm" role="group" aria-labelledby="bulk-review-confirm-title" aria-describedby="bulk-review-confirm-description">
              <h3 id="bulk-review-confirm-title">{confirming === 'accept' ? 'Accept safe suggestions?' : 'Keep current metadata?'}</h3>
              <p id="bulk-review-confirm-description">
                {confirming === 'accept'
                  ? `Only ${summary.safe_count} HIGH-confidence, non-conflicting additions will be applied to working metadata. File tags are not written here.`
                  : 'Every pending suggestion for the selected tracks will be dismissed. Track metadata and files will not change.'}
              </p>
              <div className="settings-actions">
                <button ref={confirmButtonRef} type="button" className="btn btn--primary btn--sm" disabled={busy !== null} onClick={() => void (confirming === 'accept' ? acceptSafe() : keepCurrent())}>
                  {busy ? <Loader2 size={13} className="spin" /> : <Check size={13} />}
                  {confirming === 'accept' ? 'Confirm safe acceptance' : 'Confirm keep current'}
                </button>
                <button type="button" className="btn btn--ghost btn--sm" disabled={busy !== null} onClick={cancelConfirmation}>Cancel</button>
              </div>
            </div>
          )}

          <div className="inbox-bulk-review-table table-scroll">
            <table aria-label="Selected enrichment triage">
              <thead><tr><th>Track</th><th>Artist / title</th><th>Genre</th><th>Confidence</th><th>Conflicts</th><th>Status</th></tr></thead>
              <tbody>
                {summary.rows.map((row) => (
                  <tr key={row.track_id}>
                    <td>{row.filename}</td>
                    <td><strong>{row.artist || 'Missing artist'}</strong><small>{row.title || 'Missing title'}</small></td>
                    <td>{row.genre || '—'}</td>
                    <td>{row.confidence || '—'}</td>
                    <td>{row.conflicts.length ? row.conflicts.join(', ') : 'None'}</td>
                    <td><Badge tone={stateTone(row.review_state)}>{stateLabel(row.review_state)}</Badge><small>{row.reason}</small></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {summary?.safe_count === 0 && summary.exception_count === 0 && summary.no_suggestion_count > 0 && (
        <div className="inbox-review-empty"><AlertTriangle size={14} /><p>No selected tracks have actionable suggestions.</p></div>
      )}
    </section>
  )
}
