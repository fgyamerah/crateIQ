import { FormEvent, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronUp, Library, ListMusic, Loader2, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { ApiError } from '../api/client'
import { addTrackToCrate, createCrate, deleteCrate, fetchCrate, fetchCrates, removeTrackFromCrate, reorderCrateTracks, updateCrate } from '../api/crates'
import { fetchTrackPage } from '../api/tracks'
import type { CrateDetail, CrateSummary } from '../types/crate'
import type { TrackSummary } from '../types/track'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import StatusStrip from '../components/ui/StatusStrip'
import PageHeader from '../components/PageHeader'
import AudioPreviewPlayer, { type AudioPreviewTrack } from '../components/player/AudioPreviewPlayer'
import ReviewStatusBadge from '../components/reviews/ReviewStatusBadge'
import { fetchReviewSummary, type ReviewSummary } from '../api/reviews'

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Could not complete that crate action. Please try again.'
}
function fmtDate(value: string) { try { return new Date(value).toLocaleString() } catch { return value } }
function fmtDuration(seconds: number | null) { return seconds == null ? '—' : `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}` }
function trackTitle(track: { artist: string | null; title: string | null; filename: string | null }) { return [track.artist, track.title].filter(Boolean).join(' — ') || track.filename || 'Untitled track' }

export default function Crates() {
  const [crates, setCrates] = useState<CrateSummary[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<CrateDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [notes, setNotes] = useState('')
  const [newName, setNewName] = useState('')
  const [trackQuery, setTrackQuery] = useState('')
  const [libraryTracks, setLibraryTracks] = useState<TrackSummary[]>([])
  const [loadingTracks, setLoadingTracks] = useState(false)
  const [previewTrack, setPreviewTrack] = useState<AudioPreviewTrack | null>(null)
  const [reviewSummary, setReviewSummary] = useState<ReviewSummary>({})

  const loadCrates = async (preferredId?: number | null) => {
    setLoading(true); setError(null)
    try {
      const next = await fetchCrates()
      setCrates(next)
      const id = preferredId !== undefined ? preferredId : (selectedId ?? next[0]?.id ?? null)
      setSelectedId(id)
      if (id) {
        const nextDetail = await fetchCrate(id)
        setDetail(nextDetail); setName(nextDetail.name); setNotes(nextDetail.notes ?? '')
      } else setDetail(null)
    } catch (err) { setError(messageOf(err)) }
    finally { setLoading(false) }
  }

  useEffect(() => { void loadCrates() }, []) // initial load only
  useEffect(() => {
    if (!selectedId) return
    setLoadingTracks(true)
    const timeout = window.setTimeout(() => {
      fetchTrackPage({ q: trackQuery || undefined, limit: 50, sort: 'artist' })
        .then((page) => setLibraryTracks(page.items))
        .catch((err) => setError(messageOf(err)))
        .finally(() => setLoadingTracks(false))
    }, 200)
    return () => window.clearTimeout(timeout)
  }, [selectedId, trackQuery])
  useEffect(() => {
    let cancelled = false
    const ids = [
      ...(detail?.tracks.map((track) => track.track_id) ?? []),
      ...libraryTracks.map((track) => track.id),
    ]
    fetchReviewSummary(ids)
      .then((response) => { if (!cancelled) setReviewSummary(response.reviews) })
      .catch(() => { if (!cancelled) setReviewSummary({}) })
    return () => { cancelled = true }
  }, [detail?.tracks, libraryTracks])

  const selectedTrackIds = useMemo(() => new Set(detail?.tracks.map((track) => track.track_id) ?? []), [detail])
  const refreshDetail = async (id = selectedId) => {
    if (!id) return
    const next = await fetchCrate(id)
    setDetail(next); setName(next.name); setNotes(next.notes ?? '')
    setCrates(await fetchCrates())
  }
  const submitCreate = async (event: FormEvent) => {
    event.preventDefault(); if (!newName.trim()) return
    setSaving(true); setError(null)
    try { const created = await createCrate({ name: newName.trim() }); setNewName(''); await loadCrates(created.id) }
    catch (err) { setError(messageOf(err)) } finally { setSaving(false) }
  }
  const saveMetadata = async () => {
    if (!detail || !name.trim()) return
    setSaving(true); setError(null)
    try { await updateCrate(detail.id, { name: name.trim(), notes: notes.trim() || null }); await refreshDetail() }
    catch (err) { setError(messageOf(err)) } finally { setSaving(false) }
  }
  const removeCrate = async () => {
    if (!detail || !window.confirm(`Delete “${detail.name}”? Its crate list will be removed; your music files and metadata will not change.`)) return
    setSaving(true); setError(null)
    try {
      await deleteCrate(detail.id)
      const remaining = await fetchCrates()
      setSelectedId(null); setDetail(null)
      await loadCrates(remaining[0]?.id ?? null)
    }
    catch (err) { setError(messageOf(err)) } finally { setSaving(false) }
  }
  const addTrack = async (trackId: number) => {
    if (!detail) return
    setSaving(true); setError(null)
    try { setDetail(await addTrackToCrate(detail.id, trackId)); setCrates(await fetchCrates()) }
    catch (err) { setError(messageOf(err)) } finally { setSaving(false) }
  }
  const removeTrack = async (trackId: number) => {
    if (!detail) return
    setSaving(true); setError(null)
    try { setDetail(await removeTrackFromCrate(detail.id, trackId)); setCrates(await fetchCrates()) }
    catch (err) { setError(messageOf(err)) } finally { setSaving(false) }
  }
  const moveTrack = async (index: number, direction: -1 | 1) => {
    if (!detail || index + direction < 0 || index + direction >= detail.tracks.length) return
    const trackIds = detail.tracks.map((track) => track.track_id)
    ;[trackIds[index], trackIds[index + direction]] = [trackIds[index + direction], trackIds[index]]
    setSaving(true); setError(null)
    try { setDetail(await reorderCrateTracks(detail.id, trackIds)); setCrates(await fetchCrates()) }
    catch (err) { setError(messageOf(err)) } finally { setSaving(false) }
  }

  return <main className="crate-workspace manual-crates-page">
    <PageHeader title="Manual Crates" subtitle="Build and order local DJ crates before later playlist or export workflows." />
    <StatusStrip tone="info" icon={<Library size={15} />} footnote="Local state only · no music files, tags, BPM, key, or cue data are changed.">
      Manual crates are review-first working lists for this selected library.
    </StatusStrip>
    {error && <StatusStrip tone="danger" role="alert" onDismiss={() => setError(null)}>{error}</StatusStrip>}
    <div className="manual-crates-kpis">
      <KpiCard tone="emerald" icon={<ListMusic size={18} />} label="Saved crates" value={crates.length} sub="Local to this library" />
      <KpiCard tone="violet" icon={<Library size={18} />} label="Tracks in crates" value={crates.reduce((total, crate) => total + crate.track_count, 0)} sub="No duplicates within a crate" />
    </div>
    <div className="manual-crates-layout">
      <aside className="manual-crates-list" aria-label="Crates">
        <form className="manual-crates-create" onSubmit={submitCreate}>
          <label htmlFor="new-crate-name">New crate</label>
          <div><input id="new-crate-name" className="form-input" value={newName} maxLength={120} placeholder="e.g. Afro House Warmup" onChange={(event) => setNewName(event.target.value)} /><button className="btn btn--primary btn--sm" disabled={saving || !newName.trim()}><Plus size={14} /> Create</button></div>
        </form>
        {loading ? <p className="muted"><Loader2 className="spin" size={14} /> Loading crates…</p> : crates.length === 0 ? <EmptyState icon={<ListMusic size={22} />} title="No crates yet" message="Create a local crate, then add and order tracks from your library." /> : <div className="manual-crate-list-items">{crates.map((crate) => <button key={crate.id} className={`manual-crate-list-item ${crate.id === selectedId ? 'manual-crate-list-item--selected' : ''}`} onClick={() => void loadCrates(crate.id)}><span><strong>{crate.name}</strong><small>{crate.track_count} track{crate.track_count === 1 ? '' : 's'} · updated {fmtDate(crate.updated_at)}</small></span><Badge tone={crate.track_count ? 'info' : 'pending'}>{crate.track_count}</Badge></button>)}</div>}
      </aside>
      <section className="manual-crate-detail">
        {!detail && !loading && <EmptyState icon={<ListMusic size={28} />} title="Start a crate" message="Create a crate to make a deliberate, ordered DJ working list." />}
        {detail && <>
          <div className="manual-crate-meta"><div className="manual-crate-meta-fields"><label>Name<input className="form-input" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} /></label><label>Notes<textarea className="form-input" value={notes} maxLength={1000} placeholder="Optional set context, venue notes, or transition ideas" onChange={(event) => setNotes(event.target.value)} /></label></div><div className="manual-crate-actions"><button className="btn btn--primary btn--sm" disabled={saving || !name.trim()} onClick={() => void saveMetadata()}><Pencil size={13} /> Save details</button><button className="btn btn--danger btn--sm" disabled={saving} onClick={() => void removeCrate()}><Trash2 size={13} /> Delete</button></div></div>
          <div className="manual-crate-section-head"><div><h2>Crate order</h2><p>{detail.track_count} track{detail.track_count === 1 ? '' : 's'} · updated {fmtDate(detail.updated_at)}</p></div><button className="btn btn--ghost btn--sm" disabled={saving} onClick={() => void refreshDetail()}><RefreshCw size={13} /> Refresh</button></div>
          <AudioPreviewPlayer track={previewTrack} />
          {detail.tracks.length === 0 ? <EmptyState icon={<ListMusic size={24} />} title="This crate is empty" message="Use the library finder below to add tracks. Their order stays explicit and editable." /> : <div className="manual-crate-tracks">{detail.tracks.map((track, index) => <div className="manual-crate-track" key={track.track_id}><span className="manual-crate-position">{track.position}</span><div className="manual-crate-track-main"><strong>{trackTitle(track)}</strong><span>{track.genre ?? 'Unknown genre'} · {track.bpm?.toFixed(1) ?? '—'} BPM · {fmtDuration(track.duration_sec)}{track.missing_from_library ? ' · no longer in selected library' : ''}</span></div>{track.key_camelot && <Badge tone="info">{track.key_camelot}</Badge>}<ReviewStatusBadge trackId={track.track_id} status={reviewSummary[String(track.track_id)]?.review_status} rating={reviewSummary[String(track.track_id)]?.rating} /><div className="manual-crate-track-actions"><button className="btn btn--ghost btn--xs" disabled={saving || track.missing_from_library} onClick={() => setPreviewTrack({ id: track.track_id, artist: track.artist, title: track.title, filename: track.filename, genre: track.genre, bpm: track.bpm, key_camelot: track.key_camelot, duration_sec: track.duration_sec })}>Preview</button><button className="btn btn--ghost btn--xs" disabled={saving || index === 0} aria-label={`Move ${trackTitle(track)} up`} onClick={() => void moveTrack(index, -1)}><ChevronUp size={15} /></button><button className="btn btn--ghost btn--xs" disabled={saving || index === detail.tracks.length - 1} aria-label={`Move ${trackTitle(track)} down`} onClick={() => void moveTrack(index, 1)}><ChevronDown size={15} /></button><button className="btn btn--ghost btn--xs" disabled={saving} onClick={() => void removeTrack(track.track_id)}>Remove</button></div></div>)}</div>}
          <div className="manual-library-picker"><div className="manual-crate-section-head"><div><h2>Add tracks</h2><p>Search the selected library. Adding is local and does not touch audio metadata.</p></div>{loadingTracks && <Loader2 className="spin" size={16} />}</div><input className="form-input" type="search" placeholder="Search artist, title, or filename…" value={trackQuery} onChange={(event) => setTrackQuery(event.target.value)} />{libraryTracks.length === 0 && !loadingTracks ? <p className="muted">No library tracks match this search.</p> : <div className="manual-library-results">{libraryTracks.map((track) => <div className="manual-library-result" key={track.id}><span><strong>{trackTitle(track)}</strong><small>{track.genre ?? 'Unknown genre'} · {track.bpm?.toFixed(1) ?? '—'} BPM {track.key_camelot ? `· ${track.key_camelot}` : ''}</small></span><ReviewStatusBadge trackId={track.id} status={reviewSummary[String(track.id)]?.review_status} rating={reviewSummary[String(track.id)]?.rating} /><button className="btn btn--ghost btn--xs" disabled={saving || selectedTrackIds.has(track.id)} onClick={() => void addTrack(track.id)}>{selectedTrackIds.has(track.id) ? 'Added' : 'Add'}</button></div>)}</div>}</div>
        </>}
      </section>
    </div>
  </main>
}
