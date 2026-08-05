import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Pause, Play } from 'lucide-react'
import ThreeBandWaveform from '../components/player/ThreeBandWaveform'
import { usePersistentPlayer } from '../components/player/usePersistentPlayer'
import type { PersistentPlayerTrack } from '../components/player/usePersistentPlayer'
import { apiFetch } from '../api/client'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'
import Badge from '../components/ui/Badge'
import type { BadgeTone } from '../components/ui/Badge'

type Track = {
  track_id: number
  title: string | null
  artist: string | null
  filename: string | null
  genre: string | null
  bpm: number | null
  key_camelot: string | null
  duration_sec: number | null
  review_status: string
  rating: number | null
  notes: string
}

const REVIEW_STATUSES = [
  { value: 'reviewed', label: 'Reviewed', shortcut: 'R' },
  { value: 'favorite', label: 'Favorite', shortcut: 'F' },
  { value: 'maybe', label: 'Maybe', shortcut: 'M' },
  { value: 'rejected', label: 'Rejected', shortcut: 'X' },
  { value: 'needs_work', label: 'Needs Work', shortcut: 'W' },
  { value: 'unreviewed', label: 'Unreviewed', shortcut: '—' },
] as const

function reviewLabel(status: string): string {
  return REVIEW_STATUSES.find((item) => item.value === status)?.label ?? 'Unreviewed'
}

function reviewTone(status: string): BadgeTone {
  if (status === 'favorite' || status === 'reviewed') return 'succeeded'
  if (status === 'rejected') return 'failed'
  if (status === 'maybe' || status === 'needs_work') return 'pending'
  return 'info'
}

function displayTitle(track: Track): string {
  return track.title || track.filename || 'Untitled track'
}

function toPlayerTrack(track: Track): PersistentPlayerTrack {
  return {
    id: track.track_id,
    artist: track.artist,
    title: track.title,
    filename: track.filename,
    genre: track.genre,
    bpm: track.bpm,
    key_camelot: track.key_camelot,
    duration_sec: track.duration_sec,
    sourceLabel: 'Music Review',
  }
}

export default function MusicReview() {
  const [searchParams] = useSearchParams()
  const persistentPlayer = usePersistentPlayer()
  const requestedPlayerTrackRef = useRef<number | null>(null)
  const [tracks, setTracks] = useState<Track[]>([])
  const [selected, setSelected] = useState<Track | null>(null)
  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const requestedTrackId = useMemo(() => {
    const value = Number(searchParams.get('track_id'))
    return Number.isInteger(value) && value > 0 ? value : null
  }, [searchParams])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await apiFetch.get<{ items: Track[] }>('/reviews/tracks')
      setTracks(response.items)
      setSelected(response.items.find((track) => track.track_id === requestedTrackId) ?? response.items[0] ?? null)
      setError(null)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Music Review could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [requestedTrackId])

  useEffect(() => { void load() }, [load])
  useEffect(() => { setNotes(selected?.notes ?? '') }, [selected?.track_id])

  const playerQueue = useMemo(() => tracks.map(toPlayerTrack), [tracks])

  useEffect(() => {
    if (!requestedTrackId || requestedPlayerTrackRef.current === requestedTrackId) return
    const requestedTrack = playerQueue.find((track) => track.id === requestedTrackId)
    if (!requestedTrack) return
    requestedPlayerTrackRef.current = requestedTrackId
    persistentPlayer.loadTrack(requestedTrack, playerQueue)
  }, [persistentPlayer.loadTrack, playerQueue, requestedTrackId])

  useEffect(() => {
    const playerTrack = persistentPlayer.currentTrack
    if (!playerTrack || playerTrack.sourceLabel !== 'Music Review') return
    const matchingTrack = tracks.find((track) => track.track_id === playerTrack.id)
    if (matchingTrack && matchingTrack.track_id !== selected?.track_id) setSelected(matchingTrack)
  }, [persistentPlayer.currentTrack, selected?.track_id, tracks])

  const save = async (status: string) => {
    if (!selected) return
    const next = await apiFetch.patch<Track>(`/reviews/tracks/${selected.track_id}`, {
      review_status: status,
      notes,
    })
    setTracks((current) => current.map((track) => track.track_id === next.track_id ? next : track))
    setSelected(next)
  }

  const saveRating = async (rating: number) => {
    if (!selected) return
    const next = await apiFetch.patch<Track>(`/reviews/tracks/${selected.track_id}`, { rating, notes })
    setTracks((current) => current.map((track) => track.track_id === next.track_id ? next : track))
    setSelected(next)
  }

  const selectedIndex = tracks.findIndex((track) => track.track_id === selected?.track_id)
  const selectTrack = (track: Track, autoplay = false) => {
    setSelected(track)
    persistentPlayer.loadTrack(toPlayerTrack(track), playerQueue, { autoplay })
  }

  const selectRelative = (delta: number) => {
    if (!tracks.length) return
    const nextIndex = selectedIndex + delta
    if (nextIndex < 0 || nextIndex >= tracks.length) return
    const keepPlaying = persistentPlayer.playing && persistentPlayer.currentTrack?.id === selected?.track_id
    selectTrack(tracks[nextIndex], keepPlaying)
  }

  const playSelected = () => {
    if (!selected) return
    if (persistentPlayer.currentTrack?.id === selected.track_id) {
      void persistentPlayer.togglePlayback()
      return
    }
    persistentPlayer.loadTrack(toPlayerTrack(selected), playerQueue, { autoplay: true })
  }

  const reviewCounts = useMemo(() => ({
    reviewed: tracks.filter((track) => track.review_status !== 'unreviewed').length,
    favorite: tracks.filter((track) => track.review_status === 'favorite').length,
    needsWork: tracks.filter((track) => track.review_status === 'needs_work').length,
    unreviewed: tracks.filter((track) => track.review_status === 'unreviewed').length,
  }), [tracks])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as Element | null
      if (event.metaKey || event.ctrlKey || event.altKey || event.repeat) return
      if (target?.closest('input, textarea, select, button, [contenteditable="true"], [role="textbox"]')) return
      const index = tracks.findIndex((track) => track.track_id === selected?.track_id)
      const key = event.key.toLowerCase()
      const status: Record<string, string> = {
        f: 'favorite',
        m: 'maybe',
        r: 'reviewed',
        x: 'rejected',
        w: 'needs_work',
      }
      if (status[key]) {
        event.preventDefault()
        void save(status[key])
      }
      if ((key === 'n' || event.key === 'ArrowRight') && tracks.length) {
        event.preventDefault()
        const nextIndex = index + 1
        if (nextIndex < tracks.length) {
          const keepPlaying = persistentPlayer.playing && persistentPlayer.currentTrack?.id === selected?.track_id
          selectTrack(tracks[nextIndex], keepPlaying)
        }
      }
      if ((key === 'p' || event.key === 'ArrowLeft') && tracks.length) {
        event.preventDefault()
        const previousIndex = index - 1
        if (previousIndex >= 0) {
          const keepPlaying = persistentPlayer.playing && persistentPlayer.currentTrack?.id === selected?.track_id
          selectTrack(tracks[previousIndex], keepPlaying)
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [tracks, selected, notes])

  return (
    <main className="page music-review-page">
      <PageHeader
        title="Music Review"
        subtitle="Listen, rate, and triage your local library without changing files or tags."
        badge={<Badge tone="info">DB only</Badge>}
      />
      <StatusStrip tone="info" className="music-review-safety">
        DB-only review flags, rating, and notes. No tag writes or file changes. Shortcuts: F favorite · M maybe · R reviewed · X reject · W needs work · N/P next/previous.
      </StatusStrip>

      {error && <StatusStrip tone="danger">{error}</StatusStrip>}

      <section className="music-review-kpis" aria-label="Music Review status summary">
        <div><span>Total queue</span><strong>{tracks.length}</strong><small>Local tracks</small></div>
        <div><span>Reviewed</span><strong>{reviewCounts.reviewed}</strong><small>Any saved decision</small></div>
        <div><span>Favorites</span><strong>{reviewCounts.favorite}</strong><small>Marked for recall</small></div>
        <div><span>Needs work</span><strong>{reviewCounts.needsWork}</strong><small>Follow-up queue</small></div>
        <div><span>Unreviewed</span><strong>{reviewCounts.unreviewed}</strong><small>Still to hear</small></div>
      </section>

      <div className="music-review-layout">
        <section className="music-review-queue" aria-label="Tracks awaiting music review">
          <div className="music-review-panel-head">
            <div><h2>Review queue</h2><p>Selecting a track loads it without saving a review.</p></div>
            <span>{loading ? 'Loading…' : `${tracks.length} tracks`}</span>
          </div>
          <div className="music-review-list-head" aria-hidden="true">
            <span>#</span><span>Track</span><span>Artist</span><span>BPM</span><span>Key</span><span>Review</span>
          </div>
          <div className="music-review-list">
            {tracks.map((track, index) => (
              <button
                className={`music-review-row${selected?.track_id === track.track_id ? ' is-selected' : ''}`}
                key={track.track_id}
                onClick={() => selectTrack(track)}
                aria-pressed={selected?.track_id === track.track_id}
              >
                <span className="music-review-row-index">{index + 1}</span>
                <span className="music-review-row-track">
                  <strong>{displayTitle(track)}</strong>
                  <small>{track.genre || 'Unknown genre'}</small>
                </span>
                <span>{track.artist || 'Unknown artist'}</span>
                <span className="music-review-mono">{track.bpm?.toFixed(1) ?? '—'}</span>
                <span className="music-review-key">{track.key_camelot || '—'}</span>
                <Badge tone={reviewTone(track.review_status)}>{reviewLabel(track.review_status)}{track.rating ? ` · ${track.rating}/5` : ''}</Badge>
              </button>
            ))}
            {!loading && tracks.length === 0 && <p className="music-review-empty">No tracks are available for Music Review.</p>}
          </div>
        </section>

        <aside className="music-review-detail">
          <div className="music-review-panel-head">
            <div>
              <span className="music-review-kicker">Selected track</span>
              <h2>{selected ? displayTitle(selected) : 'Nothing selected'}</h2>
              <p>{selected?.artist || 'Choose a track from the queue.'}</p>
            </div>
            {selected && <Badge tone={reviewTone(selected.review_status)}>{reviewLabel(selected.review_status)}{selected.rating ? ` · ${selected.rating}/5` : ''}</Badge>}
          </div>
          <div className="music-review-player-action">
            <button className="btn btn--primary btn--sm" type="button" disabled={!selected} onClick={playSelected}>
              {persistentPlayer.playing && persistentPlayer.currentTrack?.id === selected?.track_id
                ? <Pause size={14} fill="currentColor" />
                : <Play size={14} fill="currentColor" />}
              {!selected
                ? 'Select a track to play'
                : persistentPlayer.playing && persistentPlayer.currentTrack?.id === selected.track_id
                  ? 'Pause bottom player'
                  : persistentPlayer.currentTrack?.id === selected.track_id
                    ? 'Play current track'
                    : 'Play in bottom player'}
            </button>
            <span>Uses the visible Music Review queue · browser preview only</span>
          </div>
          <ThreeBandWaveform seed={selected?.track_id ?? 0} inactive={!selected} />
          {selected && (
            <>
              <div className="music-review-status-section">
                <div className="music-review-section-head">
                  <strong>Review status</strong>
                  <span>Keyboard shortcuts stay off while typing.</span>
                </div>
                <div className="music-review-status-actions">
                  {REVIEW_STATUSES.map((status) => (
                    <button
                      className={`music-review-status-btn music-review-status-btn--${status.value}${selected.review_status === status.value ? ' is-active' : ''}`}
                      key={status.value}
                      onClick={() => void save(status.value)}
                      aria-pressed={selected.review_status === status.value}
                    >
                      {status.label}<kbd>{status.shortcut}</kbd>
                    </button>
                  ))}
                </div>
              </div>

              <div className="music-review-fields">
                <label>
                  Rating 0–5
                  <select
                    className="form-input"
                    value={selected.rating ?? 0}
                    onChange={(event) => void saveRating(Number(event.target.value))}
                  >
                    <option value="0">0 — No rating</option>
                    {[1, 2, 3, 4, 5].map((rating) => <option key={rating} value={rating}>{rating}</option>)}
                  </select>
                </label>
                <div className="music-review-position">
                  <span>Queue position</span>
                  <strong>{selectedIndex + 1} / {tracks.length}</strong>
                </div>
              </div>

              <label className="music-review-notes">
                Notes
                <textarea
                  className="form-input"
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                  placeholder="Mix context, energy, crowd response, or follow-up notes…"
                />
              </label>

              <div className="music-review-footer-actions">
                <div>
                  <button className="btn btn--ghost btn--sm" onClick={() => selectRelative(-1)} disabled={selectedIndex <= 0}>← Previous</button>
                  <button className="btn btn--ghost btn--sm" onClick={() => selectRelative(1)} disabled={selectedIndex < 0 || selectedIndex >= tracks.length - 1}>Next →</button>
                </div>
                <button className="btn btn--primary" onClick={() => void save(selected.review_status)}>Save notes</button>
              </div>
            </>
          )}
        </aside>
      </div>
    </main>
  )
}
