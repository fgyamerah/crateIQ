import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, Globe2, Loader2, RefreshCw, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { applyEnrichmentSuggestion, fetchEnrichmentReview, refreshEnrichmentReview, runOnlineLookup, updateEnrichmentSuggestion } from '../api/enrichmentReview'
import type { EnrichmentSuggestion, EnrichmentReview } from '../types/enrichmentReview'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import StatusStrip from '../components/ui/StatusStrip'

const errorMessage = (e: unknown) => (e instanceof ApiError ? e.displayMessage : 'Could not update local enrichment review.')

/** Every field any source proposes for this track, current value first. */
function compareFields(trackItems: EnrichmentSuggestion[]): string[] {
  const seen = new Set<string>()
  for (const item of trackItems) for (const field of item.allowed_fields) seen.add(field)
  return Array.from(seen)
}

export default function EnrichmentReviewPage() {
  const [review, setReview] = useState<EnrichmentReview | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [fields, setFields] = useState<Record<string, string>>({})
  const [note, setNote] = useState('')
  const [saved, setSaved] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  const apply = useCallback((next: EnrichmentReview) => {
    setReview(next)
    const selected = next.items.find((x) => x.suggestion_id === selectedId) ?? next.items[0]
    setSelectedId(selected?.suggestion_id ?? null)
    setFields(selected?.selected_fields ?? {})
    setNote(selected?.note ?? '')
    setSaved(false)
    setConfirm(false)
  }, [selectedId])

  const load = useCallback(async () => {
    try { apply(await fetchEnrichmentReview()) } catch (e) { setProblem(errorMessage(e)) }
  }, [apply])
  useEffect(() => { void load() }, [load])

  const selected = useMemo<EnrichmentSuggestion | null>(
    () => review?.items.find((x) => x.suggestion_id === selectedId) ?? null,
    [review, selectedId],
  )
  const trackItems = useMemo<EnrichmentSuggestion[]>(
    () => (review && selected ? review.items.filter((x) => x.track_id === selected.track_id) : []),
    [review, selected],
  )
  const trackFields = useMemo(() => compareFields(trackItems), [trackItems])
  const siblingCounts = useMemo(() => {
    const counts = new Map<number, number>()
    for (const item of review?.items ?? []) counts.set(item.track_id, (counts.get(item.track_id) ?? 0) + 1)
    return counts
  }, [review])

  function select(item: EnrichmentSuggestion) {
    setSelectedId(item.suggestion_id)
    setFields(item.selected_fields)
    setNote(item.note)
    setSaved(false)
    setConfirm(false)
  }

  async function refresh() {
    setBusy(true)
    try { apply(await refreshEnrichmentReview()) } catch (e) { setProblem(errorMessage(e)) } finally { setBusy(false) }
  }

  async function save(decision: 'pending' | 'ignored' | 'review_later') {
    if (!selected) return
    setBusy(true)
    try {
      apply(await updateEnrichmentSuggestion(selected.track_id, selected.suggestion_id, { decision, note, selected_fields: fields }))
      setSaved(decision === 'pending')
      setMessage(decision === 'pending' ? 'Selected fields saved locally.' : 'Review decision saved locally.')
    } catch (e) { setProblem(errorMessage(e)) } finally { setBusy(false) }
  }

  const [lookupBusy, setLookupBusy] = useState<'beets' | 'musicbrainz' | null>(null)

  async function lookup(source: 'beets' | 'musicbrainz') {
    if (!selected) return
    setLookupBusy(source)
    try {
      const next = await runOnlineLookup(selected.track_id, source)
      apply(next)
      const found = next.items.find((x) => x.track_id === selected.track_id && x.source_id === source)
      setMessage(found ? `${source === 'beets' ? 'Beets' : 'MusicBrainz'} suggestion added for this track.` : `No new ${source} suggestion for this track — see warnings below.`)
    } catch (e) { setProblem(errorMessage(e)) } finally { setLookupBusy(null) }
  }

  async function runApply() {
    if (!selected) return
    setBusy(true)
    try {
      const result = await applyEnrichmentSuggestion(selected.track_id, selected.suggestion_id, fields)
      apply(result.review)
      setMessage(`${result.applied} local-index item applied; ${result.skipped} skipped.`)
      if (result.warnings.length) setProblem(result.warnings.join(' '))
    } catch (e) { setProblem(errorMessage(e)) } finally { setBusy(false) }
  }

  /** Only the currently selected suggestion's own candidate values are editable —
   * this is the sole path that can end up in a PATCH/apply request, so a field
   * proposed by a sibling source can never be toggled on by mistake. */
  function setField(field: string, value: string, on: boolean) {
    if (!selected) return
    setFields((current) => {
      const next = { ...current }
      if (on) next[field] = value
      else delete next[field]
      return next
    })
    setSaved(false)
    setConfirm(false)
  }

  return (
    <main className="page enrichment-review-page">
      <header className="page-header">
        <div className="page-header-left">
          <p className="page-eyebrow">Source comparison foundation</p>
          <h1>Multi-source Enrichment Review</h1>
          <p className="page-subtitle">Compare local metadata suggestions side by side across sources before applying selected fields to CrateIQ&rsquo;s local index.</p>
        </div>
        <div className="settings-header-actions">
          <Link className="btn btn--ghost btn--sm" to="/settings#metadata-sources">Metadata Sources</Link>
          <Link className="btn btn--ghost btn--sm" to="/beets-review">Beets Review</Link>
          <button className="btn btn--primary btn--sm" disabled={busy} onClick={() => void refresh()}><RefreshCw size={14} />Refresh preview</button>
        </div>
      </header>

      <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>
        Review-first · DB-only · selected fields only. No tags, file, or BPM/key/Camelot/cue changes. Beets and MusicBrainz lookups only run when you explicitly click them for a single track.
      </StatusStrip>
      {problem && <StatusStrip tone="danger" onDismiss={() => setProblem(null)}>{problem}</StatusStrip>}
      {message && <StatusStrip tone="good" onDismiss={() => setMessage(null)}>{message}</StatusStrip>}

      <section className="enrichment-source-summary">
        {review?.sources.map((source) => (
          <span key={source.id}>
            <strong>{source.label}</strong> <Badge tone={source.connection_status === 'ready' ? 'succeeded' : 'pending'}>{source.current_behavior.replace('_', ' ')}</Badge>
          </span>
        ))}
      </section>

      <section className="beets-review-kpis">
        <KpiCard tone="cyan" label="Suggestions" value={review?.summary.suggestions ?? '—'} sub="Local candidates" />
        <KpiCard tone="violet" label="Pending" value={review?.summary.pending ?? '—'} sub="Needs review" />
        <KpiCard tone="emerald" label="Applied" value={review?.summary.applied ?? '—'} sub="DB-only" />
      </section>

      {review?.warnings.map((w) => <StatusStrip key={w} tone="warn">{w}</StatusStrip>)}

      {!review || !review.items.length ? (
        <EmptyState
          title="No local comparison suggestions"
          message="Refresh preview after importing tracks, then open a track to run a Beets or MusicBrainz lookup."
          action={<button className="btn btn--primary" onClick={() => void refresh()} disabled={busy}>Refresh preview</button>}
        />
      ) : (
        <div className="beets-review-layout">
          <aside className="beets-review-candidates">
            <div className="beets-review-scroll">
              {review.items.map((item) => {
                const siblings = (siblingCounts.get(item.track_id) ?? 1) - 1
                return (
                  <button
                    className={`beets-review-option${selected?.suggestion_id === item.suggestion_id ? ' is-selected' : ''}`}
                    key={item.suggestion_id}
                    onClick={() => select(item)}
                  >
                    <span>
                      <strong>{item.current_fields.title || item.filename}</strong>
                      <small>{item.source_id} · {item.confidence} confidence{siblings > 0 ? ` · +${siblings} other source${siblings > 1 ? 's' : ''}` : ''}</small>
                    </span>
                    <Badge tone="pending">{item.decision.replace('_', ' ')}</Badge>
                  </button>
                )
              })}
            </div>
          </aside>

          {selected && (
            <section className="beets-review-detail">
              <h2>{selected.current_fields.title || selected.filename}</h2>
              <code className="beets-review-path">{selected.relative_path || selected.filename}</code>
              <StatusStrip tone="info">{selected.reason}</StatusStrip>

              <div className="enrichment-online-lookup">
                <button className="btn btn--online-lookup btn--sm" disabled={lookupBusy !== null} onClick={() => void lookup('beets')}>
                  {lookupBusy === 'beets' ? <Loader2 size={13} className="spin" /> : <Globe2 size={13} />}
                  {lookupBusy === 'beets' ? 'Looking up…' : 'Look up on Beets'}
                </button>
                <button className="btn btn--online-lookup btn--sm" disabled={lookupBusy !== null} onClick={() => void lookup('musicbrainz')}>
                  {lookupBusy === 'musicbrainz' ? <Loader2 size={13} className="spin" /> : <Globe2 size={13} />}
                  {lookupBusy === 'musicbrainz' ? 'Looking up…' : 'Look up on MusicBrainz'}
                </button>
                <span className="lib-muted">Sends this track's artist/title to the selected provider — never automatic, never a whole-library scan.</span>
              </div>

              <div className="enrichment-compare">
                <strong>Compare sources for this track</strong>
                <p className="lib-muted">Editing values from <strong>{selected.source_id}</strong>. Select a different source below to edit its values instead.</p>
                <div className="enrichment-compare-scroll">
                  <div className="enrichment-compare-table" role="table" aria-label="Current metadata compared against every candidate source">
                    <div className="enrichment-compare-row enrichment-compare-row--head" role="row">
                      <span role="columnheader">Field</span>
                      <span role="columnheader">Current</span>
                      {trackItems.map((item) => (
                        <span role="columnheader" key={item.suggestion_id}>
                          {item.source_id}{item.suggestion_id === selected.suggestion_id ? ' (editing)' : ''}
                        </span>
                      ))}
                    </div>
                    {trackFields.map((field) => (
                      <div className="enrichment-compare-row" role="row" key={field}>
                        <span role="cell"><strong>{field}</strong></span>
                        <span role="cell" className="lib-muted">{selected.current_fields[field] || 'Missing'}</span>
                        {trackItems.map((item) => {
                          const value = item.suggested_fields[field]
                          if (value === undefined) return <span role="cell" className="lib-muted" key={item.suggestion_id}>—</span>
                          if (item.suggestion_id === selected.suggestion_id) {
                            return (
                              <label className="enrichment-compare-pick" role="cell" key={item.suggestion_id}>
                                <input type="checkbox" checked={field in fields} onChange={(e) => setField(field, value, e.target.checked)} />
                                <span>{value}</span>
                              </label>
                            )
                          }
                          return (
                            <span role="cell" className="enrichment-compare-alt" key={item.suggestion_id}>
                              {value}
                              <button type="button" onClick={() => select(item)}>Edit from here</button>
                            </span>
                          )
                        })}
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <label className="beets-review-note">Review note<textarea className="form-input" value={note} onChange={(e) => { setNote(e.target.value); setSaved(false) }} /></label>

              <div className="beets-review-actions">
                <button className="btn btn--ghost btn--sm" disabled={busy || !Object.keys(fields).length} onClick={() => void save('pending')}>Save selection</button>
                <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void save('ignored')}>Ignore</button>
                <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void save('review_later')}>Review later</button>
              </div>

              <div className="beets-review-apply">
                <label className="form-check">
                  <input type="checkbox" checked={confirm} disabled={!saved} onChange={(e) => setConfirm(e.target.checked)} />
                  I understand this writes only selected empty metadata fields to CrateIQ&rsquo;s local DB.
                </label>
                {!saved && <p className="lib-muted">Click &ldquo;Save selection&rdquo; above first to enable this.</p>}
                <button className="btn btn--primary btn--sm" disabled={busy || !saved || !confirm || !Object.keys(fields).length} onClick={() => void runApply()}>
                  <Check size={13} />Apply selected fields
                </button>
              </div>
            </section>
          )}
        </div>
      )}
    </main>
  )
}
