import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bookmark, Check, Clock3, Eye, FileWarning, RefreshCw, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchQualityReview, refreshQualityReview, updateQualityReviewDecision } from '../api/qualityReview'
import type { QualityReviewDecision, QualityReviewItem, QualityReviewResponse } from '../types/qualityReview'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import StatusStrip from '../components/ui/StatusStrip'

function messageFor(error: unknown) {
  return error instanceof ApiError ? error.displayMessage : 'Could not load audio quality review.'
}

function formatBytes(value: number | null) {
  if (value == null) return 'Size unavailable'
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function formatDuration(value: number | null) {
  if (value == null) return 'Duration unavailable'
  return `${Math.floor(value / 60)}:${Math.floor(value % 60).toString().padStart(2, '0')}`
}

function decisionTone(decision: QualityReviewDecision) {
  if (decision === 'reviewed') return 'succeeded'
  if (decision === 'ignore') return 'cancelled'
  return 'pending'
}

function decisionLabel(decision: QualityReviewDecision) {
  return decision === 'review_later' ? 'Review later' : decision === 'unresolved' ? 'Unresolved' : decision[0].toUpperCase() + decision.slice(1)
}

function flagLabel(flag: string) {
  return flag.replace(/_/g, ' ')
}

export default function QualityReview() {
  const [review, setReview] = useState<QualityReviewResponse | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [savingTrack, setSavingTrack] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const applyReview = useCallback((next: QualityReviewResponse) => {
    setReview(next)
    setSelectedId((current) => next.items.some((item) => item.track_id === current) ? current : next.items[0]?.track_id ?? null)
    setNotes(Object.fromEntries(next.items.map((item) => [item.track_id, item.note])))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try { applyReview(await fetchQualityReview()) } catch (err) { setError(messageFor(err)) } finally { setLoading(false) }
  }, [applyReview])

  useEffect(() => { void load() }, [load])

  const refresh = async () => {
    setRefreshing(true)
    setError(null)
    try { applyReview(await refreshQualityReview()) } catch (err) { setError(messageFor(err)) } finally { setRefreshing(false) }
  }

  const selected = useMemo<QualityReviewItem | null>(() => review?.items.find((item) => item.track_id === selectedId) ?? null, [review, selectedId])

  const save = async (decision: QualityReviewDecision) => {
    if (!selected) return
    setSavingTrack(selected.track_id)
    setError(null)
    try { applyReview(await updateQualityReviewDecision(selected.track_id, { decision, note: notes[selected.track_id] ?? '' })) } catch (err) { setError(messageFor(err)) } finally { setSavingTrack(null) }
  }

  const summary = review?.summary
  return <main className="page quality-review-page">
    <header className="page-header quality-review-header">
      <div className="page-header-left">
        <p className="page-eyebrow">Safe review workspace</p>
        <h1>Audio Quality Review</h1>
        <p className="page-subtitle">Review bounded ffprobe findings without changing files. Decisions are stored only in CrateIQ’s local index.</p>
      </div>
      <div className="quality-review-header-actions"><Link className="btn btn--ghost btn--sm" to="/jobs">Analysis Jobs</Link><Link className="btn btn--ghost btn--sm" to="/quality">Quality</Link><button className="btn btn--primary btn--sm" disabled={refreshing} onClick={() => void refresh()}><RefreshCw size={14} /> {refreshing ? 'Refreshing…' : 'Refresh probe'}</button></div>
    </header>

    <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>Probe-only review: no transcode, remediation, tag write, music-file change, or DJ database write exists in this workflow.</StatusStrip>
    <div className="quality-review-policy-chips" aria-label="Audio quality review safety policies"><span><ShieldCheck size={13} /> DB-only review</span><span>Probe only</span><span>No transcode</span><span>No file writes</span><span>No tag writes</span></div>
    <section className="quality-review-kpis" aria-label="Audio quality review summary"><KpiCard tone="cyan" label="Tracks checked" value={summary?.tracks_checked ?? '—'} sub="Bounded ffprobe preview" /><KpiCard tone="coral" label="Findings" value={summary?.findings ?? '—'} sub="Conservative flags only" /><KpiCard tone="violet" label="Unresolved" value={summary?.unresolved ?? '—'} sub="Needs a review choice" /><KpiCard tone="emerald" label="Reviewed" value={summary?.reviewed ?? '—'} sub="Local review state" /><KpiCard tone="cyan" label="Ignored" value={summary?.ignored ?? '—'} sub="Local review state" /><KpiCard tone="violet" label="Review later" value={summary?.review_later ?? '—'} sub="Local review state" /></section>
    {error && <StatusStrip tone="danger">{error}</StatusStrip>}
    {review?.message && <StatusStrip tone="info">{review.message}</StatusStrip>}
    {review?.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}
    {review && <p className="quality-review-threshold"><SlidersHorizontal size={13} /> “Low bitrate” is a neutral candidate flag for lossy codecs below {review.low_bitrate_threshold_kbps} kbps; it is not a quality verdict.</p>}

    {loading ? <p className="muted">Loading audio quality review…</p> : !review || review.items.length === 0 ? <EmptyState icon={<FileWarning size={28} />} title="No quality findings to review" message="Run the bounded ffprobe preview to inspect readable imported tracks. It never transcodes or changes media, tags, or local track metadata." action={<button className="btn btn--primary btn--sm" disabled={refreshing} onClick={() => void refresh()}><Eye size={14} /> {refreshing ? 'Refreshing…' : 'Refresh safe probe'}</button>} /> : <div className="quality-review-layout">
      <aside className="quality-review-findings" aria-label="Audio quality findings"><div className="quality-review-panel-heading"><div><h2>Findings</h2><p>{review.latest_preview_at ? `Saved ${new Date(review.latest_preview_at).toLocaleString()}` : 'Latest safe probe'}</p></div><Badge tone="pending">Probe only</Badge></div><div className="quality-review-scroll">{review.items.map((item) => <button className={`quality-review-option${selected?.track_id === item.track_id ? ' is-selected' : ''}`} key={item.track_id} onClick={() => setSelectedId(item.track_id)} type="button"><span><strong>{item.title || item.filename}</strong><small>{item.artist || item.filename}</small></span><span>{item.flags.map((flag) => <Badge key={flag} tone={flag === 'unreadable' || flag === 'unsupported_format' ? 'failed' : 'pending'}>{flagLabel(flag)}</Badge>)}</span></button>)}</div></aside>
      {selected && <section className="quality-review-detail"><div className="quality-review-panel-heading"><div><h2>{selected.title || selected.filename}</h2><p>{selected.artist || 'Artist unavailable'} · {selected.status.replace(/_/g, ' ')}</p></div><Badge tone={decisionTone(selected.decision)}>{decisionLabel(selected.decision)}</Badge></div><code className="quality-review-path">{selected.relative_path || selected.filename}</code><div className="quality-review-metrics"><span><strong>{selected.container || '—'}</strong> container</span><span><strong>{selected.codec || '—'}</strong> codec</span><span><strong>{selected.bitrate_kbps == null ? '—' : `${selected.bitrate_kbps} kbps`}</strong> bitrate</span><span><strong>{selected.sample_rate_hz == null ? '—' : `${selected.sample_rate_hz} Hz`}</strong> sample rate</span><span><strong>{formatDuration(selected.duration_sec)}</strong> duration</span><span><strong>{selected.channels ?? '—'}</strong> channels</span><span><strong>{formatBytes(selected.file_size_bytes)}</strong> size</span></div><div className="quality-review-flags"><strong>Review flags</strong><div>{selected.flags.map((flag) => <Badge key={flag} tone={flag === 'unreadable' || flag === 'unsupported_format' ? 'failed' : 'pending'}>{flagLabel(flag)}</Badge>)}</div></div><div className="quality-review-actions"><button className="btn btn--ghost btn--xs" disabled={savingTrack === selected.track_id} onClick={() => void save('reviewed')}><Check size={12} /> Mark reviewed</button><button className="btn btn--ghost btn--xs" disabled={savingTrack === selected.track_id} onClick={() => void save('ignore')}>Ignore</button><button className="btn btn--ghost btn--xs" disabled={savingTrack === selected.track_id} onClick={() => void save('review_later')}><Clock3 size={12} /> Review later</button><button className="btn btn--ghost btn--xs" disabled={savingTrack === selected.track_id} onClick={() => void save('unresolved')}>Clear</button></div><label className="quality-review-note">Review note<textarea className="form-input" value={notes[selected.track_id] ?? ''} maxLength={1000} onChange={(event) => setNotes((current) => ({ ...current, [selected.track_id]: event.target.value }))} placeholder="Optional local note" /></label><button className="btn btn--ghost btn--xs quality-review-save-note" disabled={savingTrack === selected.track_id} onClick={() => void save(selected.decision)}><Bookmark size={12} /> {savingTrack === selected.track_id ? 'Saving…' : 'Save note'}</button></section>}
    </div>}
  </main>
}
