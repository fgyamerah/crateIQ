import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bookmark, Check, Clock3, Eye, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react'
import { ApiError } from '../api/client'
import { applyBeetsFields, fetchBeetsReview, refreshBeetsReview, updateBeetsReview } from '../api/beetsReview'
import type { BeetsReviewDecision, BeetsReviewItem, BeetsReviewResponse } from '../types/beetsReview'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import StatusStrip from '../components/ui/StatusStrip'

type DraftValues = Record<number, Record<string, string>>
type SelectedNames = Record<number, Set<string>>

function messageFor(error: unknown) {
  return error instanceof ApiError ? error.displayMessage : 'Could not load Beets enrichment review.'
}

function decisionTone(decision: BeetsReviewDecision) {
  if (decision === 'applied') return 'succeeded'
  if (decision === 'ignored') return 'cancelled'
  return 'pending'
}

function decisionLabel(decision: BeetsReviewDecision) {
  return decision === 'review_later' ? 'Review later' : decision[0].toUpperCase() + decision.slice(1)
}

function label(field: string) {
  return field === 'artist' ? 'Artist' : field === 'title' ? 'Title' : 'Genre'
}

export default function BeetsReview() {
  const [review, setReview] = useState<BeetsReviewResponse | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [draftValues, setDraftValues] = useState<DraftValues>({})
  const [selectedNames, setSelectedNames] = useState<SelectedNames>({})
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [selectionSaved, setSelectionSaved] = useState(false)
  const [confirmApply, setConfirmApply] = useState(false)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)

  const applyReview = useCallback((next: BeetsReviewResponse) => {
    setReview(next)
    setSelectedId((current) => next.items.some((item) => item.track_id === current) ? current : next.items[0]?.track_id ?? null)
    setDraftValues(Object.fromEntries(next.items.map((item) => [item.track_id, item.selected_fields])))
    setSelectedNames(Object.fromEntries(next.items.map((item) => [item.track_id, new Set(Object.keys(item.selected_fields))])))
    setNotes(Object.fromEntries(next.items.map((item) => [item.track_id, item.note])))
    setSelectionSaved(false)
    setConfirmApply(false)
  }, [])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { applyReview(await fetchBeetsReview()) } catch (err) { setError(messageFor(err)) } finally { setLoading(false) }
  }, [applyReview])

  useEffect(() => { void load() }, [load])

  const refresh = async () => {
    setRefreshing(true); setError(null); setResult(null)
    try { applyReview(await refreshBeetsReview()) } catch (err) { setError(messageFor(err)) } finally { setRefreshing(false) }
  }

  const selected = useMemo<BeetsReviewItem | null>(() => review?.items.find((item) => item.track_id === selectedId) ?? null, [review, selectedId])
  const selectedFields = useMemo(() => {
    if (!selected) return {}
    return Object.fromEntries([...selectedNames[selected.track_id] ?? new Set<string>()].map((field) => [field, draftValues[selected.track_id]?.[field]?.trim() ?? '']))
  }, [draftValues, selected, selectedNames])

  const update = async (decision: BeetsReviewDecision) => {
    if (!selected) return
    setSaving(true); setError(null); setResult(null)
    try {
      const next = await updateBeetsReview(selected.track_id, { decision, note: notes[selected.track_id] ?? '', selected_fields: selectedFields })
      applyReview(next)
      setSelectionSaved(true)
      setResult(decision === 'pending' ? 'Selection and note saved locally.' : `Marked ${decisionLabel(decision).toLowerCase()} in the local review state.`)
    } catch (err) { setError(messageFor(err)) } finally { setSaving(false) }
  }

  const apply = async () => {
    if (!selected) return
    setApplying(true); setError(null); setResult(null)
    try {
      const response = await applyBeetsFields(selected.track_id, selectedFields)
      applyReview(response.review)
      setResult(`${response.applied} track updated in CrateIQ’s local index; ${response.skipped} skipped; ${response.failed} failed.`)
      if (response.warnings.length) setError(response.warnings.join(' '))
    } catch (err) { setError(messageFor(err)) } finally { setApplying(false) }
  }

  const setValue = (field: string, value: string) => {
    if (!selected) return
    setDraftValues((current) => ({ ...current, [selected.track_id]: { ...current[selected.track_id], [field]: value } }))
    setSelectionSaved(false); setConfirmApply(false)
  }
  const toggleField = (field: string) => {
    if (!selected) return
    setSelectedNames((current) => {
      const next = new Set(current[selected.track_id] ?? [])
      next.has(field) ? next.delete(field) : next.add(field)
      return { ...current, [selected.track_id]: next }
    })
    setSelectionSaved(false); setConfirmApply(false)
  }

  const summary = review?.summary
  const canSave = Object.values(selectedFields).every(Boolean)
  const canApply = selectionSaved && confirmApply && Object.keys(selectedFields).length > 0 && canSave

  return <main className="page beets-review-page">
    <header className="page-header beets-review-header"><div className="page-header-left"><p className="page-eyebrow">Selected-field local enrichment</p><h1>Beets Enrichment Review</h1><p className="page-subtitle">Review missing metadata, then apply only fields you explicitly select to CrateIQ’s local index.</p></div><div className="beets-review-header-actions"><Link className="btn btn--ghost btn--sm" to="/jobs">Analysis Jobs</Link><Link className="btn btn--ghost btn--sm" to="/enrichment">Enrichment</Link><button className="btn btn--primary btn--sm" disabled={refreshing} onClick={() => void refresh()}><RefreshCw size={14} /> {refreshing ? 'Refreshing…' : 'Refresh candidates'}</button></div></header>
    <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>DB-only apply: no tag writes, file moves, or audio changes. BPM, musical key, Camelot, cue data, and MIK/trusted values are excluded.</StatusStrip>
    <div className="beets-review-policy-chips" aria-label="Beets enrichment safety policies"><span><ShieldCheck size={13} /> DB-only apply</span><span>Review before apply</span><span>No tag writes</span><span>No file moves</span><span>No audio changes</span><span>No BPM/key/cue changes</span></div>
    <section className="beets-review-kpis" aria-label="Beets enrichment review summary"><KpiCard tone="cyan" label="Candidates" value={summary?.candidates ?? '—'} sub="Missing local metadata" /><KpiCard tone="violet" label="Pending" value={summary?.pending ?? '—'} sub="Needs review" /><KpiCard tone="emerald" label="Applied" value={summary?.applied ?? '—'} sub="Local-index values only" /><KpiCard tone="cyan" label="Ignored" value={summary?.ignored ?? '—'} sub="Local review state" /><KpiCard tone="violet" label="Review later" value={summary?.review_later ?? '—'} sub="Local review state" /><KpiCard tone="coral" label="Fields selected" value={summary?.fields_selected ?? '—'} sub="Saved selections" /></section>
    {error && <StatusStrip tone="danger">{error}</StatusStrip>}
    {result && <StatusStrip tone="good">{result}</StatusStrip>}
    {review?.message && <StatusStrip tone="info">{review.message}</StatusStrip>}
    {review?.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}
    {loading ? <p className="muted">Loading local enrichment candidates…</p> : !review || review.items.length === 0 ? <EmptyState icon={<Sparkles size={28} />} title="No missing metadata candidates" message="Refresh the local candidate preview after importing tracks. This workflow does not invoke Beets or modify media files." action={<button className="btn btn--primary btn--sm" disabled={refreshing} onClick={() => void refresh()}><Eye size={14} /> {refreshing ? 'Refreshing…' : 'Refresh candidates'}</button>} /> : <div className="beets-review-layout"><aside className="beets-review-candidates" aria-label="Beets enrichment candidates"><div className="beets-review-panel-heading"><div><h2>Metadata candidates</h2><p>{review.latest_preview_at ? `Saved ${new Date(review.latest_preview_at).toLocaleString()}` : 'Latest local preview'}</p></div><Badge tone="pending">Review required</Badge></div><div className="beets-review-scroll">{review.items.map((item) => <button className={`beets-review-option${selected?.track_id === item.track_id ? ' is-selected' : ''}`} key={item.track_id} onClick={() => setSelectedId(item.track_id)} type="button"><span><strong>{item.current_fields.title || item.filename}</strong><small>{item.current_fields.artist || item.filename}</small></span><span><Badge tone={decisionTone(item.decision)}>{decisionLabel(item.decision)}</Badge><small>Missing {item.missing_fields.join(', ')}</small></span></button>)}</div></aside>
      {selected && <section className="beets-review-detail"><div className="beets-review-panel-heading"><div><h2>{selected.current_fields.title || selected.filename}</h2><p>{selected.current_fields.artist || 'Artist unavailable'} · selected values remain local only.</p></div><Badge tone={decisionTone(selected.decision)}>{decisionLabel(selected.decision)}</Badge></div><code className="beets-review-path">{selected.relative_path || selected.filename}</code><div className="beets-review-fields"><strong>Allowed missing fields</strong><p>Enter a value, then include it explicitly. Existing non-empty fields cannot be overwritten in this workflow.</p>{selected.allowed_fields.map((field) => <label className="beets-review-field" key={field}><input type="checkbox" checked={(selectedNames[selected.track_id] ?? new Set()).has(field)} disabled={!(draftValues[selected.track_id]?.[field] ?? '').trim()} onChange={() => toggleField(field)} /><span><strong>{label(field)}</strong><small>Current: {selected.current_fields[field] || 'Missing'}</small></span><input className="form-input" value={draftValues[selected.track_id]?.[field] ?? ''} maxLength={500} onChange={(event) => setValue(field, event.target.value)} placeholder={`Enter ${label(field).toLowerCase()}`} /></label>)}</div><div className="beets-review-actions"><button className="btn btn--ghost btn--xs" disabled={saving || !canSave} onClick={() => void update('pending')}><Bookmark size={12} /> {saving ? 'Saving…' : 'Save selection'}</button><button className="btn btn--ghost btn--xs" disabled={saving} onClick={() => void update('ignored')}>Ignore</button><button className="btn btn--ghost btn--xs" disabled={saving} onClick={() => void update('review_later')}><Clock3 size={12} /> Review later</button></div><label className="beets-review-note">Review note<textarea className="form-input" value={notes[selected.track_id] ?? ''} maxLength={1000} onChange={(event) => { setNotes((current) => ({ ...current, [selected.track_id]: event.target.value })); setSelectionSaved(false) }} placeholder="Optional local note" /></label><div className="beets-review-apply"><label className="form-check"><input type="checkbox" checked={confirmApply} disabled={!selectionSaved || Object.keys(selectedFields).length === 0} onChange={(event) => setConfirmApply(event.target.checked)} /> I understand this writes only the selected fields to CrateIQ’s local index.</label><button className="btn btn--primary btn--sm" disabled={!canApply || applying} onClick={() => void apply()}><Check size={13} /> {applying ? 'Applying…' : 'Apply selected fields'}</button><small>Music files and tags are not changed. Beets is not invoked in this foundation.</small></div></section>}
    </div>}
  </main>
}
