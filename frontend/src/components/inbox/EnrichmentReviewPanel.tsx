import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, Loader2, ShieldCheck } from 'lucide-react'
import { ApiError } from '../../api/client'
import {
  applyEnrichmentSuggestion,
  fetchInboxTrackEnrichmentReview,
  updateEnrichmentSuggestion,
} from '../../api/enrichmentReview'
import type { EnrichmentSuggestion, InboxTrackEnrichmentReview } from '../../types/enrichmentReview'
import Badge from '../ui/Badge'

interface Props {
  trackId: number
  onDecision?: () => Promise<void> | void
}

const errorMessage = (error: unknown, fallback: string) =>
  error instanceof ApiError ? error.displayMessage : fallback

function confidenceTone(confidence: string): 'pending' | 'running' | 'succeeded' | 'failed' | 'info' {
  const value = (confidence || '').toUpperCase()
  if (value === 'HIGH') return 'succeeded'
  if (value === 'MEDIUM') return 'running'
  if (value === 'CONFLICT') return 'failed'
  return 'pending'
}

function sourceLabel(id: string, sources: InboxTrackEnrichmentReview['sources']): string {
  const found = sources.find((source) => source.id === id)
  if (found) return found.label
  if (id === 'consensus_review' || id === 'consensus_apply') return 'Provider consensus'
  if (id === 'filename_hints') return 'Filename'
  return id
}

/** Ordered field names actually proposed for this suggestion (evidence first,
 *  so CONFLICT fields without a resolved value are still surfaced). */
function fieldNames(item: EnrichmentSuggestion): string[] {
  const names = new Set<string>()
  for (const field of Object.keys(item.evidence ?? {})) names.add(field)
  for (const field of Object.keys(item.suggested_fields ?? {})) names.add(field)
  return Array.from(names)
}

function display(value: string | null | undefined): string {
  return value === null || value === undefined || value === '' ? 'Missing' : value
}

export default function EnrichmentReviewPanel({ trackId, onDecision }: Props) {
  const [review, setReview] = useState<InboxTrackEnrichmentReview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [itemErrors, setItemErrors] = useState<Record<string, string>>({})
  const [selected, setSelected] = useState<Record<string, Record<string, string>>>({})
  const actionRefs = useRef<Map<string, HTMLButtonElement>>(new Map())
  const containerRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const next = await fetchInboxTrackEnrichmentReview(trackId)
      setReview(next)
      setSelected(Object.fromEntries(next.items.map((item) => [
        item.suggestion_id,
        Object.keys(item.selected_fields ?? {}).length ? item.selected_fields : { ...item.suggested_fields },
      ])))
    } catch (loadError) {
      setError(errorMessage(loadError, 'Could not load enrichment suggestions.'))
    } finally {
      setLoading(false)
    }
  }, [trackId])

  useEffect(() => { void load() }, [load])

  function toggleField(suggestionId: string, field: string, value: string, checked: boolean) {
    setSelected((current) => {
      const next = { ...(current[suggestionId] ?? {}) }
      if (checked) next[field] = value
      else delete next[field]
      return { ...current, [suggestionId]: next }
    })
  }

  async function useSuggested(item: EnrichmentSuggestion) {
    const fields = selected[item.suggestion_id] ?? {}
    if (!Object.keys(fields).length) return
    setBusy(item.suggestion_id)
    setItemErrors((current) => ({ ...current, [item.suggestion_id]: '' }))
    try {
      await updateEnrichmentSuggestion(item.track_id, item.suggestion_id, {
        decision: 'pending', note: '', selected_fields: fields,
      })
      const result = await applyEnrichmentSuggestion(item.track_id, item.suggestion_id, fields)
      if (result.applied === 0) {
        setItemErrors((current) => ({
          ...current,
          [item.suggestion_id]: result.warnings.join(' ') || 'The suggested value could not be applied.',
        }))
        await load()
        return
      }
      await load()
      await onDecision?.()
    } catch (actionError) {
      setItemErrors((current) => ({
        ...current,
        [item.suggestion_id]: errorMessage(actionError, 'Could not apply the suggested value.'),
      }))
      await load()
    } finally {
      setBusy(null)
    }
  }

  async function keepCurrent(item: EnrichmentSuggestion) {
    setBusy(item.suggestion_id)
    setItemErrors((current) => ({ ...current, [item.suggestion_id]: '' }))
    try {
      await updateEnrichmentSuggestion(item.track_id, item.suggestion_id, {
        decision: 'ignored', note: '', selected_fields: {},
      })
      await load()
      await onDecision?.()
    } catch (actionError) {
      setItemErrors((current) => ({
        ...current,
        [item.suggestion_id]: errorMessage(actionError, 'Could not dismiss the suggestion.'),
      }))
      await load()
    } finally {
      setBusy(null)
    }
  }

  // After a decision removes the current item, move focus predictably to the
  // next actionable suggestion (or the panel summary when none remain).
  useEffect(() => {
    if (!review) return
    if (actionRefs.current.size === 0) {
      containerRef.current?.querySelector<HTMLElement>('[data-review-summary]')?.focus()
      return
    }
    const firstId = review.items[0]?.suggestion_id
    if (firstId) actionRefs.current.get(firstId)?.focus()
  }, [review])

  const items = review?.items ?? []
  const summary = `${items.length} suggestion${items.length === 1 ? '' : 's'} need review`

  if (loading) {
    return (
      <div className="inbox-review-panel" role="status">
        <Loader2 size={14} className="spin" aria-hidden="true" /> Loading enrichment suggestions…
      </div>
    )
  }

  return (
    <div className="inbox-review-panel" ref={containerRef}>
      <p className="inbox-review-summary" tabIndex={-1} data-review-summary>
        {items.length ? summary : 'No suggestions need review'}
      </p>

      {error && (
        <div className="inbox-review-error" role="alert">
          <AlertTriangle size={14} aria-hidden="true" /> {error}
        </div>
      )}

      {!items.length && !error && (
        <div className="inbox-review-empty">
          <ShieldCheck size={16} aria-hidden="true" />
          <p>There are no actionable enrichment suggestions for this track.</p>
        </div>
      )}

      {items.length > 0 && (
        <div className="inbox-review-action-area" aria-label="Primary review actions">
          {items.map((item) => {
            const fields = fieldNames(item)
            const selectable = fields.filter((field) => item.suggested_fields?.[field] !== undefined)
            const chosen = selected[item.suggestion_id] ?? {}
            const canApply = selectable.length > 0 && Object.keys(chosen).length > 0 && busy !== item.suggestion_id
            const itemBusy = busy === item.suggestion_id
            return (
              <div className="inbox-review-action-item" key={item.suggestion_id}>
                <div className="inbox-review-action-context">
                  <strong>{sourceLabel(item.source_id, review?.sources ?? [])}</strong>
                  <Badge tone={confidenceTone(item.confidence)}>{item.confidence.toUpperCase()}</Badge>
                </div>
                <div className="inbox-review-actions">
                  <button
                    type="button"
                    className="btn btn--primary btn--sm"
                    disabled={!canApply}
                    onClick={() => void useSuggested(item)}
                    ref={(node) => {
                      if (node) actionRefs.current.set(item.suggestion_id, node)
                      else actionRefs.current.delete(item.suggestion_id)
                    }}
                  >
                    {itemBusy ? <Loader2 size={13} className="spin" aria-hidden="true" /> : <Check size={13} aria-hidden="true" />}
                    Use Suggested{selectable.length ? ` (${selectable.length})` : ''}
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    disabled={itemBusy}
                    onClick={() => void keepCurrent(item)}
                  >
                    Keep Current
                  </button>
                </div>
                {itemErrors[item.suggestion_id] && (
                  <div className="inbox-review-error" role="alert">
                    <AlertTriangle size={14} aria-hidden="true" /> {itemErrors[item.suggestion_id]}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {items.length > 0 && (
        <div className="inbox-review-details" aria-label="Review evidence and field details">
          {items.map((item) => {
            const fields = fieldNames(item)
            const conflictFields = fields.filter((field) => item.suggested_fields?.[field] === undefined)
            const chosen = selected[item.suggestion_id] ?? {}
            const itemBusy = busy === item.suggestion_id
            return (
              <section className="inbox-review-card" key={item.suggestion_id} aria-label={`Enrichment suggestion from ${sourceLabel(item.source_id, review?.sources ?? [])}`}>
                <header className="inbox-review-card-head">
                  <strong>{sourceLabel(item.source_id, review?.sources ?? [])} evidence</strong>
                  <small className="lib-muted">{item.reason}</small>
                </header>

                <div className="inbox-review-fields">
                  {fields.map((field) => {
                    const suggested = item.suggested_fields?.[field]
                    const evidence = item.evidence?.[field] ?? []
                    const isConflict = suggested === undefined && evidence.length > 0
                    const fieldLabel = field[0].toUpperCase() + field.slice(1)
                    return (
                      <div className="inbox-review-field" key={field}>
                        <div className="inbox-review-field-head">
                          <span>{fieldLabel}</span>
                          {isConflict && <Badge tone="failed">CONFLICT</Badge>}
                        </div>
                        <dl className="inbox-review-values">
                          <dt>Current</dt>
                          <dd>{display(item.current_fields?.[field])}</dd>
                          {suggested !== undefined && (
                            <>
                              <dt>Suggested</dt>
                              <dd className="inbox-review-suggested">{suggested}</dd>
                            </>
                          )}
                        </dl>
                        {isConflict && evidence.length > 0 && (
                          <ul className="inbox-review-evidence" aria-label={`Conflicting source values for ${fieldLabel}`}>
                            {evidence.map((line) => {
                              const separator = line.indexOf(': ')
                              const provider = separator > 0 ? line.slice(0, separator) : line
                              const value = separator > 0 ? line.slice(separator + 2) : line
                              return (
                                <li key={line}>
                                  <span className="lib-muted">{provider}</span> {value}
                                </li>
                              )
                            })}
                          </ul>
                        )}
                        {!isConflict && evidence.length > 0 && (
                          <p className="inbox-review-evidence-note lib-muted">
                            {evidence.length} source{evidence.length === 1 ? '' : 's'} agree
                          </p>
                        )}
                        {suggested !== undefined && (
                          <label className="inbox-review-pick">
                            <input
                              type="checkbox"
                              checked={chosen[field] === suggested}
                              disabled={itemBusy}
                              onChange={(event) => toggleField(item.suggestion_id, field, suggested, event.target.checked)}
                            />
                            <span>Use suggested {fieldLabel.toLowerCase()}</span>
                          </label>
                        )}
                      </div>
                    )
                  })}
                </div>

                {conflictFields.length > 0 && (
                  <p className="inbox-review-conflict-note lib-muted">
                    {conflictFields.map((field) => field[0].toUpperCase() + field.slice(1)).join(', ')}: sources
                    disagree, so no single value is proposed. Resolve here by keeping the current value, or edit it in
                    Metadata.
                  </p>
                )}
              </section>
            )
          })}
        </div>
      )}
    </div>
  )
}
