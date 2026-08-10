import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bookmark, Check, Clock3, Eye, FileWarning, RefreshCw, ShieldCheck } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchDuplicateReview, refreshDuplicateReview, updateDuplicateReviewDecision } from '../api/duplicates'
import type { DuplicateDecision, DuplicateReviewGroup, DuplicateReviewResponse } from '../types/duplicates'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import StatusStrip from '../components/ui/StatusStrip'

function messageFor(error: unknown) {
  return error instanceof ApiError ? error.displayMessage : 'Could not load duplicate review.'
}

function formatBytes(value: number | null) {
  if (value == null) return 'Size unavailable'
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function formatDuration(seconds: number | null) {
  if (seconds == null) return null
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function decisionTone(decision: DuplicateDecision) {
  if (decision === 'keep') return 'succeeded'
  if (decision === 'ignore') return 'cancelled'
  if (decision === 'review_later') return 'pending'
  return 'pending'
}

function decisionLabel(decision: DuplicateDecision) {
  return decision === 'review_later' ? 'Review later' : decision === 'unresolved' ? 'Unresolved' : decision[0].toUpperCase() + decision.slice(1)
}

export default function Duplicates() {
  const [review, setReview] = useState<DuplicateReviewResponse | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [savingTrack, setSavingTrack] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const applyReview = useCallback((next: DuplicateReviewResponse) => {
    setReview(next)
    setSelectedId((current) => next.groups.some((group) => group.group_id === current) ? current : next.groups[0]?.group_id ?? null)
    setNotes(Object.fromEntries(next.groups.flatMap((group) => group.items.map((item) => [item.track_id, item.note]))))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try { applyReview(await fetchDuplicateReview()) } catch (err) { setError(messageFor(err)) } finally { setLoading(false) }
  }, [applyReview])

  useEffect(() => { void load() }, [load])

  const refresh = async () => {
    setRefreshing(true)
    setError(null)
    try { applyReview(await refreshDuplicateReview()) } catch (err) { setError(messageFor(err)) } finally { setRefreshing(false) }
  }

  const selected = useMemo<DuplicateReviewGroup | null>(() => review?.groups.find((group) => group.group_id === selectedId) ?? null, [review, selectedId])

  const save = async (trackId: number, decision: DuplicateDecision) => {
    if (!selected) return
    setSavingTrack(trackId)
    setError(null)
    try {
      applyReview(await updateDuplicateReviewDecision(selected.group_id, trackId, { decision, note: notes[trackId] ?? '' }))
    } catch (err) { setError(messageFor(err)) } finally { setSavingTrack(null) }
  }

  const summary = review?.summary
  return <main className="page duplicates-page">
    <header className="page-header duplicates-header">
      <div className="page-header-left">
        <p className="page-eyebrow">Safe review workspace</p>
        <h1>Duplicate Review</h1>
        <p className="page-subtitle">Review duplicate candidates without changing files. Decisions are stored only in CrateIQ’s local index.</p>
      </div>
      <div className="duplicates-header-actions">
        <Link className="btn btn--ghost btn--sm" to="/duplicate-resolution-plan">Resolution Plan</Link>
        <Link className="btn btn--ghost btn--sm" to="/jobs">Analysis Jobs</Link>
        <Link className="btn btn--ghost btn--sm" to="/settings">Settings</Link>
        <button className="btn btn--primary btn--sm" disabled={refreshing} onClick={() => void refresh()}><RefreshCw size={14} /> {refreshing ? 'Refreshing…' : 'Refresh preview'}</button>
      </div>
    </header>

    <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>DB-only review: no delete, move, rename, quarantine, tag write, or music-file change exists in this workflow.</StatusStrip>
    <div className="duplicates-policy-chips" aria-label="Duplicate review safety policies">
      <span><ShieldCheck size={13} /> DB-only review</span><span>No delete</span><span>No move</span><span>No rename</span><span>No quarantine</span><span>No tag writes</span>
    </div>

    <section className="duplicates-kpis" aria-label="Duplicate review summary">
      <KpiCard tone="cyan" label="Groups" value={summary?.groups ?? '—'} sub="From the latest safe preview" />
      <KpiCard tone="violet" label="Candidates" value={summary?.candidates ?? '—'} sub="No actions are automatic" />
      <KpiCard tone="coral" label="Unresolved" value={summary?.unresolved ?? '—'} sub="Needs a review choice" />
      <KpiCard tone="emerald" label="Keep" value={summary?.keep ?? '—'} sub="Local review state" />
      <KpiCard tone="cyan" label="Ignored" value={summary?.ignore ?? '—'} sub="Local review state" />
      <KpiCard tone="violet" label="Review later" value={summary?.review_later ?? '—'} sub="Local review state" />
    </section>

    {error && <StatusStrip tone="danger">{error}</StatusStrip>}
    {review?.message && <StatusStrip tone="info">{review.message}</StatusStrip>}
    {review?.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}

    {loading ? <p className="muted">Loading duplicate review…</p> : !review || review.groups.length === 0 ? <EmptyState icon={<FileWarning size={28} />} title="No duplicate groups to review" message="Run a safe rmlint preview to inspect duplicate candidates. The preview never makes file or tag changes." action={<button className="btn btn--primary btn--sm" disabled={refreshing} onClick={() => void refresh()}><Eye size={14} /> {refreshing ? 'Refreshing…' : 'Refresh safe preview'}</button>} /> : <div className="duplicates-review-layout">
      <aside className="duplicates-group-list" aria-label="Duplicate groups">
        <div className="duplicates-panel-heading"><div><h2>Duplicate groups</h2><p>{review.latest_preview_at ? `Saved ${new Date(review.latest_preview_at).toLocaleString()}` : 'Latest safe preview'}</p></div><Badge tone="pending">Preview only</Badge></div>
        <div className="duplicates-group-scroll">
          {review.groups.map((group) => <button className={`duplicates-group-option${selected?.group_id === group.group_id ? ' is-selected' : ''}`} key={group.group_id} onClick={() => setSelectedId(group.group_id)} type="button">
            <span><strong>{group.group_id}</strong><small>{group.reason}</small></span><span><Badge tone={group.confidence === 'high' ? 'succeeded' : 'pending'}>{group.confidence}</Badge><small>{group.items.length} tracks</small></span>
          </button>)}
        </div>
      </aside>
      {selected && <section className="duplicates-detail-panel">
        <div className="duplicates-panel-heading"><div><h2>{selected.group_id}</h2><p>{selected.reason} · {selected.confidence} confidence · match basis: {selected.match_basis}{selected.checksum_prefix ? ` (${selected.checksum_prefix}…)` : ''} · decisions do not alter files.</p></div><Badge tone="pending">DB-only</Badge></div>
        <div className={`duplicates-recommendation${selected.recommendation.track_id == null ? ' is-unknown' : ''}`}>
          {selected.recommendation.track_id == null
            ? <p><strong>No recommendation.</strong> Evidence does not unambiguously identify a single canonical file in this group — this is advisory only, not a required action.</p>
            : <p><strong>Advisory keeper suggestion:</strong> {selected.items.find((item) => item.track_id === selected.recommendation.track_id)?.filename ?? `track ${selected.recommendation.track_id}`} <span className="duplicates-reason-code">({selected.recommendation.reason_code})</span>. This is a suggestion only — no file is removed automatically, and the review decision below is separate from this recommendation.</p>}
          {selected.recommendation.evidence.map((line) => <p key={line} className="duplicates-recommendation-evidence">{line}</p>)}
        </div>
        <div className="duplicates-track-list">
          {selected.items.map((item) => <article className="duplicates-track" key={item.track_id}>
            <div className="duplicates-track-main">
              <div className="duplicates-track-title"><strong>{item.title || item.filename}</strong><Badge tone={decisionTone(item.decision)}>{decisionLabel(item.decision)}</Badge>{selected.recommendation.track_id === item.track_id && <Badge tone="succeeded">Suggested keeper</Badge>}{item.copy_marker && <Badge tone="pending">Copy-style filename</Badge>}</div>
              <p>{item.artist || 'Artist unavailable'} · {formatBytes(item.size_bytes)}{formatDuration(item.duration_sec) ? ` · ${formatDuration(item.duration_sec)}` : ''}{item.format ? ` · .${item.format}` : ''}</p>
              <code>{item.relative_path || item.filename}</code>
              <div className="duplicates-evidence-chips">
                {item.genre && <span>{item.genre}</span>}
                {item.bpm != null && <span>{item.bpm} BPM</span>}
                {(item.key_camelot || item.key_musical) && <span>{item.key_camelot || item.key_musical}</span>}
                {item.missing_metadata.map((field) => <span key={field} className="duplicates-evidence-chip--missing">Missing {field}</span>)}
              </div>
            </div>
            <div className="duplicates-track-review"><div className="duplicates-decision-actions"><button className="btn btn--ghost btn--xs" disabled={savingTrack === item.track_id} onClick={() => void save(item.track_id, 'keep')}><Check size={12} /> Mark keep</button><button className="btn btn--ghost btn--xs" disabled={savingTrack === item.track_id} onClick={() => void save(item.track_id, 'ignore')}>Ignore</button><button className="btn btn--ghost btn--xs" disabled={savingTrack === item.track_id} onClick={() => void save(item.track_id, 'review_later')}><Clock3 size={12} /> Review later</button><button className="btn btn--ghost btn--xs" disabled={savingTrack === item.track_id} onClick={() => void save(item.track_id, 'unresolved')}>Clear</button></div>
              <label>Review note<textarea className="form-input" value={notes[item.track_id] ?? ''} maxLength={1000} onChange={(event) => setNotes((current) => ({ ...current, [item.track_id]: event.target.value }))} placeholder="Optional local note" /></label><button className="btn btn--ghost btn--xs" disabled={savingTrack === item.track_id} onClick={() => void save(item.track_id, item.decision)}><Bookmark size={12} /> {savingTrack === item.track_id ? 'Saving…' : 'Save note'}</button>
            </div>
          </article>)}
        </div>
      </section>}
    </div>}
  </main>
}
