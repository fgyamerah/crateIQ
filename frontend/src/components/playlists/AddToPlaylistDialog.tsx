import { useEffect, useRef, useState } from 'react'
import { Check, ListMusic, Loader2, Plus, X } from 'lucide-react'
import { ApiError } from '../../api/client'
import {
  addToPlaylist,
  createUserPlaylist,
  fetchUserPlaylists,
  previewAddToPlaylist,
} from '../../api/userPlaylists'
import type { PlaylistAddPreview, UserPlaylistSummary } from '../../types/userPlaylist'
import StatusStrip from '../ui/StatusStrip'

interface Props {
  trackIds: number[]
  onClose: () => void
  onApplied?: () => void
}

function messageFor(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.displayMessage : error instanceof Error ? error.message : fallback
}

export default function AddToPlaylistDialog({ trackIds, onClose, onApplied }: Props) {
  const [playlists, setPlaylists] = useState<UserPlaylistSummary[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [preview, setPreview] = useState<PlaylistAddPreview | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'load' | 'create' | 'preview' | 'apply' | null>('load')
  const [createOpen, setCreateOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const firstFocusRef = useRef<HTMLSelectElement>(null)

  const uniqueTrackIds = [...new Set(trackIds)]
  const isBulk = uniqueTrackIds.length > 1

  useEffect(() => {
    let cancelled = false
    fetchUserPlaylists()
      .then((items) => {
        if (cancelled) return
        setPlaylists(items)
        setSelectedId(items[0]?.id ?? null)
      })
      .catch((err) => { if (!cancelled) setError(messageFor(err, 'Could not load playlists.')) })
      .finally(() => { if (!cancelled) setBusy(null) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    firstFocusRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const createPlaylist = async () => {
    if (!newName.trim()) return
    setBusy('create'); setError(null)
    try {
      const created = await createUserPlaylist({ name: newName.trim(), description: newDescription.trim() || null })
      setPlaylists((current) => [created, ...current])
      setSelectedId(created.id)
      setNewName(''); setNewDescription(''); setCreateOpen(false)
      window.dispatchEvent(new Event('crateiq:user-playlists-changed'))
    } catch (err) { setError(messageFor(err, 'Could not create playlist.')) }
    finally { setBusy(null) }
  }

  const previewAdd = async () => {
    if (!selectedId) return
    setBusy('preview'); setError(null); setResult(null)
    try { setPreview(await previewAddToPlaylist(selectedId, uniqueTrackIds)) }
    catch (err) { setError(messageFor(err, 'Could not preview this playlist add.')) }
    finally { setBusy(null) }
  }

  const applyAdd = async () => {
    if (!selectedId || (isBulk && !preview)) return
    setBusy('apply'); setError(null)
    try {
      const applied = await addToPlaylist(selectedId, uniqueTrackIds, isBulk)
      setResult(applied.message)
      onApplied?.()
      window.dispatchEvent(new Event('crateiq:user-playlists-changed'))
    } catch (err) { setError(messageFor(err, 'Could not add tracks to the playlist.')) }
    finally { setBusy(null) }
  }

  return (
    <div className="playlist-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="playlist-dialog" role="dialog" aria-modal="true" aria-labelledby="add-to-playlist-title" aria-describedby="add-to-playlist-description">
        <header className="playlist-dialog-header">
          <div>
            <span className="playlist-dialog-kicker"><ListMusic size={13} /> Playlist membership</span>
            <h2 id="add-to-playlist-title">Add to Playlist</h2>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close Add to Playlist dialog"><X size={17} /></button>
        </header>
        <div className="playlist-dialog-body">
          <p id="add-to-playlist-description" className="muted">Tracks are referenced in place. No audio files or metadata will change.</p>
          <p className="playlist-dialog-selection"><strong>{uniqueTrackIds.length} selected</strong>{isBulk ? ' · choose a destination, preview, then apply' : ' · choose a destination'}</p>
          {error && <StatusStrip tone="danger" role="alert">{error}</StatusStrip>}
          {result && <StatusStrip tone="good" icon={<Check size={14} />} actions={<button type="button" className="btn btn--ghost btn--xs" onClick={onClose}>Done</button>}>{result}</StatusStrip>}
          {busy === 'load' ? <p className="muted"><Loader2 size={14} className="spin" /> Loading playlists…</p> : (
            <>
              {playlists.length > 0 && (
                <label className="playlist-dialog-field">Add to
                  <select ref={firstFocusRef} className="form-input" value={selectedId ?? ''} onChange={(event) => { setSelectedId(Number(event.target.value)); setPreview(null); setResult(null) }} disabled={busy !== null || Boolean(result)}>
                    {playlists.map((playlist) => <option key={playlist.id} value={playlist.id}>{playlist.name}</option>)}
                  </select>
                </label>
              )}
              {!playlists.length && !createOpen && <p className="playlist-dialog-empty">No playlists yet. Create one to get started.</p>}
              {isBulk && preview && !result && (
                <div className="playlist-add-preview" aria-label="Add to Playlist preview">
                  <strong>{preview.selected_count} selected</strong>
                  <span>{preview.will_add_count} will be added</span>
                  <span>{preview.already_present_count} already present</span>
                  {preview.missing_count > 0 && <span>{preview.missing_count} not found in this library</span>}
                </div>
              )}
              {!result && (
                <div className="playlist-dialog-actions">
                  {isBulk
                    ? <button type="button" className="btn btn--ghost btn--sm" disabled={!selectedId || busy !== null} onClick={() => void previewAdd()}>{busy === 'preview' ? <Loader2 size={13} className="spin" /> : null} Preview</button>
                    : null}
                  <button type="button" className="btn btn--primary btn--sm" disabled={!selectedId || busy !== null || (isBulk && !preview)} onClick={() => void applyAdd()}>{busy === 'apply' ? <Loader2 size={13} className="spin" /> : <Check size={13} />} {isBulk ? 'Apply' : 'Add track'}</button>
                  <button type="button" className="btn btn--ghost btn--sm" disabled={busy !== null} onClick={() => setCreateOpen((open) => !open)}><Plus size={13} /> New playlist</button>
                  <button type="button" className="btn btn--ghost btn--sm" disabled={busy !== null} onClick={onClose}>Cancel</button>
                </div>
              )}
              {createOpen && !result && (
                <div className="playlist-inline-create">
                  <label>Playlist name<input className="form-input" value={newName} maxLength={120} autoFocus onChange={(event) => setNewName(event.target.value)} /></label>
                  <label>Description <span className="muted">(optional)</span><textarea className="form-input" value={newDescription} maxLength={1000} rows={2} onChange={(event) => setNewDescription(event.target.value)} /></label>
                  <button type="button" className="btn btn--ghost btn--sm" disabled={!newName.trim() || busy !== null} onClick={() => void createPlaylist()}>{busy === 'create' ? 'Creating…' : 'Create playlist'}</button>
                </div>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  )
}
