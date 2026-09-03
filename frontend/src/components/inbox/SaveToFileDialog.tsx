import { useEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle2, CircleAlert, FileCheck2, Loader2, ShieldCheck, X } from 'lucide-react'
import { ApiError } from '../../api/client'
import { applyTagWritePlan, fetchTagWritePlan } from '../../api/tagWrite'
import type { TagWritePlan, TagWritePlanItem } from '../../types/tagWrite'
import {
  chunkTrackIds,
  mergeTagWritePlans,
  summarizeTagWriteResults,
  type SaveToFileSummary,
} from './saveToFilePlan'

interface Props {
  trackIds: number[]
  onClose: () => void
  onApplied: () => void
}

function messageFor(error: unknown, fallback: string) {
  if (error instanceof ApiError) return error.displayMessage
  if (error instanceof Error && error.message) return error.message
  return fallback
}

function formatBytes(bytes: number) {
  if (bytes === 0) return '0 KB'
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatName(item: TagWritePlanItem) {
  return item.filename ?? item.relative_path ?? `Track ${item.track_id}`
}

function formatFormat(item: TagWritePlanItem) {
  const name = item.filename ?? item.relative_path ?? ''
  const extension = name.includes('.') ? name.split('.').pop() : null
  return extension ? extension.toUpperCase() : 'Unknown format'
}

function fieldLabel(field: string) {
  return field[0].toUpperCase() + field.slice(1)
}

function countLabel(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

function FieldBreakdown({ plan }: { plan: TagWritePlan }) {
  const fields = plan.writable_fields
    .map((field) => ({
      field,
      count: plan.items.filter((item) => !item.blocked && item.fields.some((change) => change.field === field)).length,
    }))
    .filter(({ count }) => count > 0)
  if (!fields.length) return null
  return (
    <div className="save-to-file-breakdown" aria-label="Fields that will be written">
      {fields.map(({ field, count }) => <span key={field}><strong>{fieldLabel(field)}</strong> {count} track{count === 1 ? '' : 's'}</span>)}
    </div>
  )
}

function PlanDetails({ plan }: { plan: TagWritePlan }) {
  return (
    <details className="save-to-file-details">
      <summary>Review per-track details ({plan.items.length})</summary>
      <div className="save-to-file-detail-list">
        {plan.items.map((item) => (
          <article className="save-to-file-detail" key={item.track_id}>
            <div className="save-to-file-detail-heading">
              <strong>{formatName(item)}</strong>
              <span>{formatFormat(item)}</span>
            </div>
            {item.blocked ? (
              <p className="save-to-file-detail-reason save-to-file-detail-reason--blocked">Write blocked: {item.blocker}</p>
            ) : item.fields.length ? (
              <ul>
                {item.fields.map((change) => (
                  <li key={change.field}>
                    <strong>{fieldLabel(change.field)}</strong>: {change.current_file_value ?? 'Empty'} → <strong>{change.approved_value}</strong>
                  </li>
                ))}
              </ul>
            ) : <p className="muted">Already matches the approved working metadata.</p>}
          </article>
        ))}
      </div>
    </details>
  )
}

function ResultSummary({ summary }: { summary: SaveToFileSummary }) {
  return (
    <section className="save-to-file-result" aria-live="polite" aria-labelledby="save-to-file-result-title">
      <h3 id="save-to-file-result-title">{summary.failed || summary.blocked ? <CircleAlert size={16} /> : <CheckCircle2 size={16} />} Save to File result</h3>
      <div className="save-to-file-result-grid">
        <span><strong>Saved to file: {summary.saved}</strong></span>
        <span><strong>Already current: {summary.already_current}</strong></span>
        <span><strong>Write blocked: {summary.blocked}</strong></span>
        <span><strong>Write failed: {summary.failed}</strong></span>
      </div>
      {(summary.failed > 0 || summary.blocked > 0) && (
        <ul className="save-to-file-result-errors">
          {summary.outcomes.filter((outcome) => outcome.status === 'failed' || outcome.status === 'blocked').map((outcome) => (
            <li key={outcome.track_id}><strong>{outcome.filename ?? `Track ${outcome.track_id}`}</strong> — {outcome.reason}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default function SaveToFileDialog({ trackIds, onClose, onApplied }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const trackKey = useMemo(() => trackIds.join(','), [trackIds])
  const [plan, setPlan] = useState<TagWritePlan | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<SaveToFileSummary | null>(null)

  useEffect(() => {
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeRef.current?.focus()
    return () => { returnFocusRef.current?.focus() }
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !applying) {
        event.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [applying, onClose])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setPlan(null)
    setResult(null)
    setConfirming(false)
    const loadPlan = async () => {
      try {
        const plans: TagWritePlan[] = []
        for (const chunk of chunkTrackIds(trackIds)) {
          plans.push(await fetchTagWritePlan(chunk))
        }
        if (!cancelled) setPlan(mergeTagWritePlans(plans))
      } catch (err) {
        if (!cancelled) setError(messageFor(err, 'Could not build the Save to File preview.'))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void loadPlan()
    return () => { cancelled = true }
  }, [trackKey])

  const doApply = async () => {
    if (!plan || !plan.changeable_count) return
    setApplying(true)
    setError(null)
    const results = []
    const requestErrors: Record<number, string> = {}
    try {
      for (const items of chunkTrackIds(plan.items.map((item) => item.track_id))) {
        const chunkItems = plan.items.filter((item) => items.includes(item.track_id))
        const writableItems = chunkItems.filter((item) => !item.blocked && item.fields.length)
        if (!writableItems.length) continue
        try {
          // No-op and blocked items remain represented by the reviewed plan;
          // only items that actually require a write are sent to apply.
          const response = await applyTagWritePlan(writableItems)
          results.push(...response.results)
        } catch (err) {
          const reason = messageFor(err, 'The Save to File request failed.')
          for (const item of writableItems) {
            if (!item.blocked && item.fields.length) requestErrors[item.track_id] = reason
          }
        }
      }
      const summary = summarizeTagWriteResults(plan, results, requestErrors)
      setResult(summary)
      setConfirming(false)
      onApplied()
    } finally {
      setApplying(false)
    }
  }

  return (
    <div className="save-to-file-backdrop" role="presentation">
      <section className="save-to-file-dialog" role="dialog" aria-modal="true" aria-labelledby="save-to-file-title" aria-describedby="save-to-file-description">
        <header className="save-to-file-header">
          <div>
            <span className="save-to-file-kicker"><FileCheck2 size={14} /> File tag write-back</span>
            <h2 id="save-to-file-title">Save to File{plan ? ` — ${plan.track_count} selected` : ''}</h2>
          </div>
          <button ref={closeRef} type="button" className="icon-btn" onClick={onClose} disabled={applying} aria-label="Close Save to File preview"><X size={17} /></button>
        </header>
        <div className="save-to-file-body">
          <p id="save-to-file-description" className="muted">Preview the exact differences between approved working metadata and the live managed Inbox file tags.</p>
          {loading && <p className="save-to-file-loading"><Loader2 size={15} className="spin" /> Building read-only preview…</p>}
          {error && <p className="save-to-file-error" role="alert">{error}</p>}
          {plan && !result && (
            <>
              <div className="save-to-file-summary" aria-label="Save to File preview summary">
                <span><strong>{plan.changeable_count}</strong> {countLabel(plan.changeable_count, 'track')} {plan.changeable_count === 1 ? 'has' : 'have'} changes</span>
                <span><strong>{plan.no_op_count}</strong> {countLabel(plan.no_op_count, 'track')} {plan.no_op_count === 1 ? 'already matches' : 'already match'}</span>
                <span><strong>{plan.blocked_count}</strong> {countLabel(plan.blocked_count, 'track')} cannot be written</span>
              </div>
              <FieldBreakdown plan={plan} />
              <p className="save-to-file-backup-note"><ShieldCheck size={14} /> CrateIQ will create and verify backups before writing the approved fields. Estimated backup space: {formatBytes(plan.backup_space_estimate_bytes)}.</p>
              <PlanDetails plan={plan} />
              {confirming && plan.changeable_count > 0 && (
                <section className="save-to-file-confirm" role="alertdialog" aria-labelledby="save-to-file-confirm-title" aria-describedby="save-to-file-confirm-description">
                  <h3 id="save-to-file-confirm-title">Confirm Save to File</h3>
                  <p id="save-to-file-confirm-description">Only managed Inbox copies will be modified. External source originals will not be modified. CrateIQ will create backups where the existing writer requires them, then write and verify the file tags.</p>
                  <div className="settings-actions">
                    <button type="button" className="btn btn--primary btn--sm" onClick={() => void doApply()} disabled={applying}>{applying ? 'Writing and verifying…' : 'Save to File'}</button>
                    <button type="button" className="btn btn--ghost btn--sm" onClick={() => setConfirming(false)} disabled={applying}>Cancel</button>
                  </div>
                </section>
              )}
            </>
          )}
          {plan && !result && !confirming && (
            <div className="save-to-file-actions">
              <button type="button" className="btn btn--primary" onClick={() => setConfirming(true)} disabled={!plan.changeable_count}>Review &amp; Save</button>
              {!plan.changeable_count && <span className="muted">There are no writable metadata changes in this selection.</span>}
              <button type="button" className="btn btn--ghost" onClick={onClose}>Cancel</button>
            </div>
          )}
          {result && <ResultSummary summary={result} />}
          {result && <div className="save-to-file-actions"><button type="button" className="btn btn--primary" onClick={onClose}>Done</button></div>}
        </div>
      </section>
    </div>
  )
}
