import { FormEvent, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, ChevronDown, ChevronUp, Heart, ListMusic, Loader2, Pencil, Play, Plus, Search, Trash2, X } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import {
  createUserPlaylist,
  deleteUserPlaylist,
  fetchUserPlaylist,
  fetchUserPlaylists,
  removeFromPlaylist,
  removeSelectedFromPlaylist,
  reorderPlaylist,
  updateUserPlaylist,
} from '../api/userPlaylists'
import { updateTrackReview } from '../api/reviews'
import { fetchTrack } from '../api/tracks'
import type { TrackDetail } from '../types/track'
import type { UserPlaylistDetail, UserPlaylistSort, UserPlaylistSummary, UserPlaylistTrack } from '../types/userPlaylist'
import { usePersistentPlayer } from '../components/player/usePersistentPlayer'
import type { PersistentPlayerTrack } from '../components/player/usePersistentPlayer'
import TrackInspector from '../components/library/TrackInspector'
import RatingFavoriteControls from '../components/reviews/RatingFavoriteControls'
import AddToPlaylistDialog from '../components/playlists/AddToPlaylistDialog'
import EmptyState from '../components/ui/EmptyState'
import StatusStrip from '../components/ui/StatusStrip'
import PageHeader from '../components/PageHeader'

function messageFor(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.displayMessage : error instanceof Error ? error.message : fallback
}

function trackTitle(track: { artist: string | null; title: string | null; filename: string | null }) {
  return [track.artist, track.title].filter(Boolean).join(' — ') || track.filename || 'Untitled track'
}

function formatDate(value: string) {
  try { return new Date(value).toLocaleString() } catch { return value }
}

function sortedTracks(tracks: UserPlaylistTrack[], sort: UserPlaylistSort, order: 'asc' | 'desc') {
  if (sort === 'manual') return tracks
  const reverse = order === 'desc'
  if (sort === 'rating') {
    const rated = tracks.filter((track) => track.rating !== null)
    const unrated = tracks.filter((track) => track.rating === null)
    rated.sort((left, right) => {
      const result = (left.rating ?? 0) - (right.rating ?? 0)
      return result === 0 ? left.position - right.position : (reverse ? -result : result)
    })
    return [...rated, ...unrated]
  }
  return [...tracks].sort((left, right) => {
    let a: string | number = ''
    let b: string | number = ''
    if (sort === 'favorite') { a = left.favorite ? 1 : 0; b = right.favorite ? 1 : 0 }
    else { a = (left[sort] ?? '').toString().toLowerCase(); b = (right[sort] ?? '').toString().toLowerCase() }
    if (a < b) return reverse ? 1 : -1
    if (a > b) return reverse ? -1 : 1
    return left.position - right.position
  })
}

function playlistTrackToPlayer(track: UserPlaylistTrack): PersistentPlayerTrack {
  return {
    id: track.track_id,
    artist: track.artist,
    title: track.title,
    filename: track.filename,
    genre: track.genre,
    bpm: track.bpm,
    key_camelot: track.key_camelot,
    duration_sec: track.duration_sec,
    sourceLabel: 'Playlist',
  }
}

function CreatePlaylistPanel({ onCreated, onCancel }: { onCreated: (playlist: UserPlaylistSummary) => void; onCancel: () => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!name.trim()) return
    setBusy(true); setError(null)
    try { onCreated(await createUserPlaylist({ name: name.trim(), description: description.trim() || null })) }
    catch (err) { setError(messageFor(err, 'Could not create playlist.')) }
    finally { setBusy(false) }
  }
  return (
    <form className="playlist-create-panel" onSubmit={(event) => void submit(event)} aria-label="Create playlist">
      <div className="playlist-create-fields">
        <label>Playlist name<input className="form-input" autoFocus maxLength={120} value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Warm Up" /></label>
        <label>Description <span className="muted">(optional)</span><textarea className="form-input" maxLength={1000} rows={2} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Set, mood, event, or workflow" /></label>
      </div>
      {error && <StatusStrip tone="danger" role="alert">{error}</StatusStrip>}
      <div className="playlist-panel-actions"><button type="submit" className="btn btn--primary btn--sm" disabled={!name.trim() || busy}>{busy ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} Create playlist</button><button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={onCancel}>Cancel</button></div>
    </form>
  )
}

function PlaylistRow({ playlist, onOpen, onRename, onDelete }: { playlist: UserPlaylistSummary; onOpen: () => void; onRename: (name: string) => Promise<void>; onDelete: () => void }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(playlist.name)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    if (!name.trim() || name.trim() === playlist.name) { setEditing(false); return }
    setBusy(true)
    try { await onRename(name.trim()); setEditing(false) } finally { setBusy(false) }
  }
  return (
    <article className="playlist-index-row">
      <div className="playlist-index-row-main">
        <span className="playlist-index-icon" aria-hidden="true"><ListMusic size={16} /></span>
        <div>
          {editing ? <input className="form-input playlist-inline-name" value={name} maxLength={120} autoFocus onChange={(event) => setName(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void save() } if (event.key === 'Escape') setEditing(false) }} /> : <button type="button" className="playlist-index-name" onClick={onOpen}>{playlist.name}</button>}
          <p>{playlist.track_count} track{playlist.track_count === 1 ? '' : 's'} · Updated {formatDate(playlist.updated_at)}</p>
          {playlist.description && <span className="playlist-index-description">{playlist.description}</span>}
        </div>
      </div>
      <div className="playlist-index-actions">
        {editing ? <><button type="button" className="btn btn--primary btn--xs" disabled={busy || !name.trim()} onClick={() => void save()}>Save</button><button type="button" className="btn btn--ghost btn--xs" disabled={busy} onClick={() => setEditing(false)}>Cancel</button></> : <><button type="button" className="btn btn--ghost btn--xs" onClick={onOpen}>Open</button><button type="button" className="icon-btn icon-btn--sm" onClick={() => setEditing(true)} aria-label={`Rename ${playlist.name}`} title="Rename playlist"><Pencil size={14} /></button><button type="button" className="icon-btn icon-btn--sm icon-btn--danger" onClick={onDelete} aria-label={`Delete ${playlist.name}`} title="Delete playlist"><Trash2 size={14} /></button></>}
      </div>
    </article>
  )
}

export default function Playlists() {
  const navigate = useNavigate()
  const { playlistId } = useParams()
  const detailId = playlistId && /^\d+$/.test(playlistId) ? Number(playlistId) : null
  const [playlists, setPlaylists] = useState<UserPlaylistSummary[]>([])
  const [detail, setDetail] = useState<UserPlaylistDetail | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [inspected, setInspected] = useState<TrackDetail | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<UserPlaylistSort>('manual')
  const [order, setOrder] = useState<'asc' | 'desc'>('asc')
  const [favoriteOnly, setFavoriteOnly] = useState(false)
  const [selectedTracks, setSelectedTracks] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [addTrackIds, setAddTrackIds] = useState<number[] | null>(null)
  const persistentPlayer = usePersistentPlayer()

  const loadIndex = async () => {
    const next = await fetchUserPlaylists()
    setPlaylists(next)
    return next
  }

  const loadDetail = async (id: number) => {
    setLoading(true); setError(null)
    try { setDetail(await fetchUserPlaylist(id)); setSelectedTracks(new Set()) }
    catch (err) { setError(messageFor(err, 'Could not load this playlist.')) }
    finally { setLoading(false) }
  }

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true); setError(null)
      try {
        const next = await fetchUserPlaylists()
        if (cancelled) return
        setPlaylists(next)
        if (detailId !== null) {
          const nextDetail = await fetchUserPlaylist(detailId)
          if (!cancelled) setDetail(nextDetail)
        } else setDetail(null)
      } catch (err) { if (!cancelled) setError(messageFor(err, 'Could not load playlists.')) }
      finally { if (!cancelled) setLoading(false) }
    }
    void load()
    return () => { cancelled = true }
  }, [detailId])

  useEffect(() => {
    const onChange = () => { void loadIndex() }
    window.addEventListener('crateiq:user-playlists-changed', onChange)
    return () => window.removeEventListener('crateiq:user-playlists-changed', onChange)
  }, [])

  useEffect(() => {
    if (selectedId === null) { setInspected(null); return }
    let cancelled = false
    fetchTrack(selectedId).then((track) => { if (!cancelled) setInspected(track) }).catch(() => { if (!cancelled) setInspected(null) })
    return () => { cancelled = true }
  }, [selectedId])

  const playerQueue = useMemo(() => (detail?.tracks ?? []).filter((track) => !track.missing_from_library).map(playlistTrackToPlayer), [detail?.tracks])
  const visibleTracks = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const matches = (detail?.tracks ?? []).filter((track) => {
      if (favoriteOnly && !track.favorite) return false
      if (!needle) return true
      return [track.artist, track.title, track.filename, track.genre].filter(Boolean).join(' ').toLowerCase().includes(needle)
    })
    return sortedTracks(matches, sort, order)
  }, [detail?.tracks, favoriteOnly, order, search, sort])

  const updateList = async (next: UserPlaylistSummary) => {
    setPlaylists((current) => current.map((item) => item.id === next.id ? next : item))
    if (detail?.id === next.id) setDetail((current) => current ? { ...current, ...next } : current)
    window.dispatchEvent(new Event('crateiq:user-playlists-changed'))
  }

  const deletePlaylist = async (playlist: UserPlaylistSummary) => {
    if (!window.confirm(`Delete “${playlist.name}”? The playlist will be deleted, but its tracks will remain in CrateIQ.`)) return
    setBusy(true); setError(null)
    try {
      await deleteUserPlaylist(playlist.id)
      const next = await loadIndex()
      if (detailId === playlist.id) navigate('/playlists')
      else if (!next.length) setShowCreate(false)
      window.dispatchEvent(new Event('crateiq:user-playlists-changed'))
    } catch (err) { setError(messageFor(err, 'Could not delete playlist.')) }
    finally { setBusy(false) }
  }

  const playTrack = (track: UserPlaylistTrack) => {
    if (track.missing_from_library) return
    const item = playlistTrackToPlayer(track)
    if (persistentPlayer.currentTrack?.id === item.id) void persistentPlayer.togglePlayback()
    else persistentPlayer.loadTrack(item, playerQueue, { autoplay: true })
  }

  const saveReview = async (trackId: number, patch: { rating?: number | null; favorite?: boolean }) => {
    const next = await updateTrackReview(trackId, patch)
    setDetail((current) => current ? { ...current, tracks: current.tracks.map((track) => track.track_id === trackId ? { ...track, rating: next.rating, favorite: next.favorite } : track) } : current)
    setInspected((current) => current && current.id === trackId ? { ...current, rating: next.rating, favorite: next.favorite } : current)
  }

  const removeOne = async (trackId: number) => {
    if (!detail) return
    setBusy(true); setError(null)
    try { setDetail(await removeFromPlaylist(detail.id, trackId)); setSelectedTracks((current) => { const next = new Set(current); next.delete(trackId); return next }); setPlaylists(await loadIndex()) }
    catch (err) { setError(messageFor(err, 'Could not remove this track from the playlist.')) }
    finally { setBusy(false) }
  }

  const removeSelected = async () => {
    if (!detail || !selectedTracks.size || !window.confirm(`Remove ${selectedTracks.size} selected track${selectedTracks.size === 1 ? '' : 's'} from “${detail.name}”? The tracks will remain in CrateIQ.`)) return
    setBusy(true); setError(null)
    try { setDetail(await removeSelectedFromPlaylist(detail.id, [...selectedTracks])); setSelectedTracks(new Set()); setPlaylists(await loadIndex()) }
    catch (err) { setError(messageFor(err, 'Could not remove the selected tracks.')) }
    finally { setBusy(false) }
  }

  const moveTrack = async (trackId: number, direction: -1 | 1) => {
    if (!detail || sort !== 'manual' || search || favoriteOnly) return
    const ids = detail.tracks.map((track) => track.track_id)
    const index = ids.indexOf(trackId)
    if (index < 0 || index + direction < 0 || index + direction >= ids.length) return
    ;[ids[index], ids[index + direction]] = [ids[index + direction], ids[index]]
    setBusy(true); setError(null)
    try { setDetail(await reorderPlaylist(detail.id, ids)); setPlaylists(await loadIndex()) }
    catch (err) { setError(messageFor(err, 'Could not save playlist order.')) }
    finally { setBusy(false) }
  }

  if (!detailId) {
    return <main className="playlist-page">
      <PageHeader title="Playlists" subtitle="Manual collections for sets, moods, events, and workflows." actions={<button type="button" className="btn btn--primary btn--sm" onClick={() => setShowCreate((open) => !open)}><Plus size={14} /> New Playlist</button>} />
      <StatusStrip tone="info" icon={<ListMusic size={15} />} footnote="Favorites remains a separate smart collection. Playlists only store references to tracks in this library.">Build a playlist without changing files, tags, BPM, key, or cue data.</StatusStrip>
      {error && <StatusStrip tone="danger" role="alert">{error}</StatusStrip>}
      {showCreate && <CreatePlaylistPanel onCancel={() => setShowCreate(false)} onCreated={(created) => { setPlaylists((current) => [created, ...current]); setShowCreate(false); navigate(`/playlists/${created.id}`); window.dispatchEvent(new Event('crateiq:user-playlists-changed')) }} />}
      {loading ? <p className="muted playlist-loading"><Loader2 size={14} className="spin" /> Loading playlists…</p> : playlists.length === 0 ? <EmptyState icon={<ListMusic size={24} />} title="No playlists yet" message="Create a playlist to group tracks for a set, mood, event, or workflow." action={<button type="button" className="btn btn--primary btn--sm" onClick={() => setShowCreate(true)}><Plus size={14} /> Create a playlist</button>} /> : <section className="playlist-index-list" aria-label="User-created playlists">{playlists.map((playlist) => <PlaylistRow key={playlist.id} playlist={playlist} onOpen={() => navigate(`/playlists/${playlist.id}`)} onRename={async (name) => { try { await updateList(await updateUserPlaylist(playlist.id, { name })) } catch (err) { setError(messageFor(err, 'Could not rename playlist.')); throw err } }} onDelete={() => void deletePlaylist(playlist)} />)}</section>}
    </main>
  }

  if (loading && !detail) return <main className="playlist-page"><p className="muted playlist-loading"><Loader2 size={14} className="spin" /> Loading playlist…</p></main>
  if (!detail) return <main className="playlist-page"><StatusStrip tone="danger" role="alert">{error ?? 'Playlist not found.'}</StatusStrip><button type="button" className="btn btn--ghost btn--sm" onClick={() => navigate('/playlists')}><ArrowLeft size={14} /> Back to Playlists</button></main>

  return <main className="playlist-page playlist-detail-page">
    <PageHeader title={detail.name} subtitle={detail.description || 'Manual playlist · track references only.'} actions={<><button type="button" className="btn btn--ghost btn--sm" onClick={() => navigate('/playlists')}><ArrowLeft size={14} /> All Playlists</button><button type="button" className="btn btn--ghost btn--sm" onClick={() => selectedId !== null && setAddTrackIds([selectedId])} disabled={selectedId === null}><Plus size={14} /> Add track</button><button type="button" className="btn btn--ghost btn--sm" onClick={() => void deletePlaylist(detail)}><Trash2 size={14} /> Delete</button></>} />
    {error && <StatusStrip tone="danger" role="alert">{error}</StatusStrip>}
    <div className="playlist-detail-meta"><span><strong>{detail.track_count}</strong> tracks</span><span>Created {formatDate(detail.created_at)}</span><span>Updated {formatDate(detail.updated_at)}</span><button type="button" className="btn btn--ghost btn--xs" onClick={async () => { const name = window.prompt('Rename playlist', detail.name); if (name?.trim()) { try { await updateList(await updateUserPlaylist(detail.id, { name: name.trim() })) } catch (err) { setError(messageFor(err, 'Could not rename playlist.')) } } }}><Pencil size={12} /> Rename</button></div>
    <div className="playlist-detail-toolbar">
      <label className="playlist-search"><Search size={14} aria-hidden="true" /><span className="sr-only">Search within playlist</span><input className="form-input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search artist, title, filename…" /></label>
      <label className="playlist-sort">Sort<select className="form-input" value={sort} onChange={(event) => setSort(event.target.value as UserPlaylistSort)}><option value="manual">Manual order</option><option value="artist">Artist</option><option value="title">Title</option><option value="rating">Rating</option><option value="favorite">Favorite</option></select></label>
      {sort !== 'manual' && <button type="button" className="btn btn--ghost btn--sm" onClick={() => setOrder((current) => current === 'asc' ? 'desc' : 'asc')} aria-label={`Sort ${order === 'asc' ? 'descending' : 'ascending'}`}>{order === 'asc' ? 'Ascending' : 'Descending'}</button>}
      <label className="playlist-favorite-filter"><input type="checkbox" checked={favoriteOnly} onChange={(event) => setFavoriteOnly(event.target.checked)} /> <Heart size={13} aria-hidden="true" /> Favorites only</label>
      {selectedTracks.size > 0 && <button type="button" className="btn btn--ghost btn--sm playlist-remove-selected" disabled={busy} onClick={() => void removeSelected()}><Trash2 size={13} /> Remove selected ({selectedTracks.size})</button>}
    </div>
    <div className="playlist-detail-layout">
      <section className="playlist-track-panel" aria-label={`${detail.name} tracks`}>
        <div className="playlist-track-panel-head"><strong>{visibleTracks.length} shown</strong><span>{sort === 'manual' ? 'Stored order' : 'Temporary sort · order unchanged'}</span></div>
        {visibleTracks.length === 0 ? <EmptyState icon={<ListMusic size={22} />} title={detail.tracks.length ? 'No matching tracks' : 'This playlist is empty'} message={detail.tracks.length ? 'Try another search or filter.' : 'Add tracks from Inbox, Library, or Track Inspector.'} /> : <div className="playlist-track-table-wrap"><table className="playlist-track-table"><thead><tr><th><span className="sr-only">Select</span></th><th>#</th><th>Track</th><th>BPM</th><th>Key</th><th>Signals</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{visibleTracks.map((track, index) => <tr key={track.track_id} className={selectedId === track.track_id ? 'is-selected' : ''}>
          <td><input type="checkbox" checked={selectedTracks.has(track.track_id)} onChange={() => setSelectedTracks((current) => { const next = new Set(current); if (next.has(track.track_id)) next.delete(track.track_id); else next.add(track.track_id); return next })} aria-label={`Select ${trackTitle(track)}`} /></td>
          <td className="playlist-position">{track.position}</td>
          <td><button type="button" className="playlist-track-name" onClick={() => setSelectedId(track.track_id)}><strong>{trackTitle(track)}</strong><span>{track.genre || track.filename || 'Metadata unavailable'}</span></button>{track.missing_from_library && <span className="playlist-missing">Missing from active library</span>}</td>
          <td>{track.bpm ?? '—'}</td><td>{track.key_camelot || track.key_musical || '—'}</td>
          <td><RatingFavoriteControls compact rating={track.rating} favorite={track.favorite} onRatingChange={(rating) => saveReview(track.track_id, { rating })} onFavoriteChange={(favorite) => saveReview(track.track_id, { favorite })} /></td>
          <td><div className="playlist-row-actions"><button type="button" className="icon-btn icon-btn--sm" disabled={track.missing_from_library} onClick={() => playTrack(track)} aria-label={`Play ${trackTitle(track)}`} title="Play"><Play size={13} fill="currentColor" /></button><button type="button" className="btn btn--ghost btn--xs" onClick={() => setSelectedId(track.track_id)}>Inspect</button>{sort === 'manual' && !search && !favoriteOnly && <><button type="button" className="icon-btn icon-btn--sm" disabled={busy || index === 0} onClick={() => void moveTrack(track.track_id, -1)} aria-label={`Move ${trackTitle(track)} up`} title="Move up"><ChevronUp size={14} /></button><button type="button" className="icon-btn icon-btn--sm" disabled={busy || index === visibleTracks.length - 1} onClick={() => void moveTrack(track.track_id, 1)} aria-label={`Move ${trackTitle(track)} down`} title="Move down"><ChevronDown size={14} /></button></> }<button type="button" className="icon-btn icon-btn--sm icon-btn--danger" disabled={busy} onClick={() => void removeOne(track.track_id)} aria-label={`Remove ${trackTitle(track)} from playlist`} title="Remove from Playlist"><X size={14} /></button></div></td>
        </tr>)}</tbody></table></div>}
      </section>
      <div className="playlist-inspector-rail">{inspected ? <TrackInspector track={inspected} loading={false} isCurrentTrack={persistentPlayer.currentTrack?.id === inspected.id} isPlaying={persistentPlayer.playing && persistentPlayer.currentTrack?.id === inspected.id} onPlay={() => { const track = detail.tracks.find((item) => item.track_id === inspected.id); if (track) playTrack(track) }} onReviewChange={(_trackId, patch) => saveReview(inspected.id, patch)} onAddToPlaylist={() => setAddTrackIds([inspected.id])} /> : <div className="playlist-inspector-empty"><ListMusic size={22} /><strong>Track Inspector</strong><span>Select a track to inspect metadata, preview audio, and manage your signals.</span></div>}</div>
    </div>
    {addTrackIds && <AddToPlaylistDialog trackIds={addTrackIds} onClose={() => setAddTrackIds(null)} onApplied={() => { setAddTrackIds(null); if (detailId !== null) void loadDetail(detailId) }} />}
  </main>
}
