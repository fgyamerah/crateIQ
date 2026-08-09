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

function keyFor(item: QualityReviewItem) {
  return item.finding_key ?? `ffprobe:${item.track_id}`
}

export default function QualityReview() {
  const [review, setReview] = useState<QualityReviewResponse | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const applyReview = useCallback((next: QualityReviewResponse) => {
    setReview(next)
    setSelectedId((current) => next.items.some((item) => keyFor(item) === current) ? current : (next.items[0] ? keyFor(next.items[0]) : null))
    setNotes(Object.fromEntries(next.items.map((item) => [keyFor(item), item.note])))
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

  const selected = useMemo<QualityReviewItem | null>(
    () => review?.items.find((item) => keyFor(item) === selectedId) ?? null,
    [review, selectedId],
  )

  const save = async (decision: QualityReviewDecision) => {
    if (!selected) return
    const selectedKey = keyFor(selected)
    setSavingKey(selectedKey)
    setError(null)
    try {
      applyReview(await updateQualityReviewDecision(selected.track_id, {
        decision, note: notes[selectedKey] ?? '', finding_key: selected.finding_key,
      }))
    } catch (err) { setError(messageFor(err)) } finally { setSavingKey(null) }
  }

  const summary = review?.summary
  const selectedKey = selected ? keyFor(selected) : null
  const isDurable = selected != null && selected.reason_code != null
  return <main className="page quality-review-page">
    <header className="page-header quality-review-header">
      <div className="page-header-left">
        <p className="page-eyebrow">Safe review workspace</p>
        <h1>Audio Quality Review</h1>
        <p className="page-subtitle">Review bounded ffprobe findings and durable analysis-derived findings without changing files. Decisions are stored only in CrateIQ’s local index.</p>
      </div>
      <div className="quality-review-header-actions"><Link className="btn btn--ghost btn--sm" to="/jobs">Analysis Jobs</Link><Link className="btn btn--ghost btn--sm" to="/quality">Quality</Link><button className="btn btn--primary btn--sm" disabled={refreshing} onClick={() => void refresh()}><RefreshCw size={14} /> {refreshing ? 'Refreshing…' : 'Refresh probe'}</button></div>
    </header>

    <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>Probe-only review: no transcode, remediation, tag write, music-file change, or DJ database write exists in this workflow.</StatusStrip>
    <div className="quality-review-policy-chips" aria-label="Audio quality review safety policies"><span><ShieldCheck size={13} /> DB-only review</span><span>Probe only</span><span>No transcode</span><span>No file writes</span><span>No tag writes</span></div>
    <section className="quality-review-kpis" aria-label="Audio quality review summary"><KpiCard tone="cyan" label="Tracks checked" value={summary?.tracks_checked ?? '—'} sub="Bounded ffprobe preview" /><KpiCard tone="coral" label="Findings" value={summary?.findings ?? '—'} sub="ffprobe + durable" /><KpiCard tone="violet" label="Unresolved" value={summary?.unresolved ?? '—'} sub="Needs a review choice" /><KpiCard tone="emerald" label="Reviewed" value={summary?.reviewed ?? '—'} sub="Local review state" /><KpiCard tone="cyan" label="Ignored" value={summary?.ignored ?? '—'} sub="Local review state" /><KpiCard tone="violet" label="Review later" value={summary?.review_later ?? '—'} sub="Local review state" /></section>
    {error && <StatusStrip tone="danger">{error}</StatusStrip>}
    {review?.message && <StatusStrip tone="info">{review.message}</StatusStrip>}
    {review?.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}
    {review && <p className="quality-review-threshold"><SlidersHorizontal size={13} /> “Low bitrate” is a neutral candidate flag for lossy codecs below {review.low_bitrate_threshold_kbps} kbps; it is not a quality verdict.</p>}

    {loading ? <p className="muted">Loading audio quality review…</p> : !review || review.items.length === 0 ? <EmptyState icon={<FileWarning size={28} />} title="No quality findings to review" message="Run the bounded ffprobe preview to inspect readable imported tracks. It never transcodes or changes media, tags, or local track metadata." action={<button className="btn btn--primary btn--sm" disabled={refreshing} onClick={() => void refresh()}><Eye size={14} /> {refreshing ? 'Refreshing…' : 'Refresh safe probe'}</button>} /> : <div className="quality-review-layout">
      <aside className="quality-review-findings" aria-label="Audio quality findings"><div className="quality-review-panel-heading"><div><h2>Findings</h2><p>{review.latest_preview_at ? `Saved ${new Date(review.latest_preview_at).toLocaleString()}` : 'Latest safe probe'}</p></div><Badge tone="pending">Probe only</Badge></div><div className="quality-review-scroll">{review.items.map((item) => <button className={`quality-review-option${selectedKey === keyFor(item) ? ' is-selected' : ''}`} key={keyFor(item)} onClick={() => setSelectedId(keyFor(item))} type="button"><span><strong>{item.title || item.filename}</strong><small>{item.artist || item.filename}</small></span><span>{item.flags.length > 0 ? item.flags.map((flag) => <Badge key={flag} tone={flag === 'unreadable' || flag === 'unsupported_format' ? 'failed' : 'pending'}>{flagLabel(flag)}</Badge>) : item.reason_code ? <Badge tone={item.severity === 'error' ? 'failed' : 'pending'}>{flagLabel(item.reason_code)}</Badge> : null}</span></button>)}</div></aside>
      {selected && <section className="quality-review-detail"><div className="quality-review-panel-heading"><div><h2>{selected.title || selected.filename}</h2><p>{selected.artist || 'Artist unavailable'} · {isDurable ? (selected.source || 'analysis').replace(/_/g, ' ') : (selected.status || '').replace(/_/g, ' ')}</p></div><Badge tone={decisionTone(selected.decision)}>{decisionLabel(selected.decision)}</Badge></div>
        {isDurable ? <div className="quality-review-durable-finding">
          <p className="quality-review-durable-message">{selected.message}</p>
          <div className="quality-review-flags"><strong>Finding</strong><div><Badge tone={selected.severity === 'error' ? 'failed' : 'pending'}>{flagLabel(selected.reason_code ?? '')}</Badge>{!selected.blocking && <Badge tone="info">Non-blocking</Badge>}</div></div>
          <div className="quality-review-metrics quality-review-metrics--durable"><span><strong>{selected.occurrence_count ?? 1}</strong> occurrence(s)</span><span><strong>{selected.first_seen_at ? new Date(selected.first_seen_at).toLocaleDateString() : '—'}</strong> first seen</span><span><strong>{selected.last_seen_at ? new Date(selected.last_seen_at).toLocaleString() : '—'}</strong> last seen</span></div>
        </div> : <>
          <code className="quality-review-path">{selected.relative_path || selected.filename}</code>
          <div className="quality-review-metrics"><span><strong>{selected.container || '—'}</strong> container</span><span><strong>{selected.codec || '—'}</strong> codec</span><span><strong>{selected.bitrate_kbps == null ? '—' : `${selected.bitrate_kbps} kbps`}</strong> bitrate</span><span><strong>{selected.sample_rate_hz == null ? '—' : `${selected.sample_rate_hz} Hz`}</strong> sample rate</span><span><strong>{formatDuration(selected.duration_sec)}</strong> duration</span><span><strong>{selected.channels ?? '—'}</strong> channels</span><span><strong>{formatBytes(selected.file_size_bytes)}</strong> size</span></div>
          <div className="quality-review-flags"><strong>Review flags</strong><div>{selected.flags.map((flag) => <Badge key={flag} tone={flag === 'unreadable' || flag === 'unsupported_format' ? 'failed' : 'pending'}>{flagLabel(flag)}</Badge>)}</div></div>
        </>}
        <div className="quality-review-actions"><button className="btn btn--ghost btn--xs" disabled={savingKey === selectedKey} onClick={() => void save('reviewed')}><Check size={12} /> Mark reviewed</button><button className="btn btn--ghost btn--xs" disabled={savingKey === selectedKey} onClick={() => void save('ignore')}>Ignore</button><button className="btn btn--ghost btn--xs" disabled={savingKey === selectedKey} onClick={() => void save('review_later')}><Clock3 size={12} /> Review later</button><button className="btn btn--ghost btn--xs" disabled={savingKey === selectedKey} onClick={() => void save('unresolved')}>Clear</button></div><label className="quality-review-note">Review note<textarea className="form-input" value={notes[selectedKey ?? ''] ?? ''} maxLength={1000} onChange={(event) => setNotes((current) => ({ ...current, [selectedKey ?? '']: event.target.value }))} placeholder="Optional local note" /></label><button className="btn btn--ghost btn--xs quality-review-save-note" disabled={savingKey === selectedKey} onClick={() => void save(selected.decision)}><Bookmark size={12} /> {savingKey === selectedKey ? 'Saving…' : 'Save note'}</button></section>}
    </div>}
  </main>
}
