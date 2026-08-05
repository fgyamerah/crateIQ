import { useEffect, useState } from 'react'
import AudioPreviewPlayer from '../components/player/AudioPreviewPlayer'
import { apiFetch } from '../api/client'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'
import Badge from '../components/ui/Badge'

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

export default function MusicReview() {
  const [tracks, setTracks] = useState<Track[]>([])
  const [selected, setSelected] = useState<Track | null>(null)
  const [notes, setNotes] = useState('')

  const load = async () => {
    const response = await apiFetch.get<{ items: Track[] }>('/reviews/tracks')
    setTracks(response.items)
    setSelected(response.items[0] || null)
  }

  useEffect(() => { void load() }, [])

  const save = async (status: string) => {
    if (!selected) return
    const next = await apiFetch.patch<Track>(`/reviews/tracks/${selected.track_id}`, {
      review_status: status,
      notes,
    })
    setTracks((current) => current.map((track) => track.track_id === next.track_id ? next : track))
    setSelected(next)
  }

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      if (target.matches('input,textarea,select,button')) return
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
        setSelected(tracks[(index + 1 + tracks.length) % tracks.length])
      }
      if ((key === 'p' || event.key === 'ArrowLeft') && tracks.length) {
        event.preventDefault()
        setSelected(tracks[(index - 1 + tracks.length) % tracks.length])
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [tracks, selected, notes])

  return (
    <main className="page">
      <PageHeader title="Music Review" subtitle="Listen, rate, and mark tracks without changing files or tags." />
      <StatusStrip tone="info">
        DB-only review flags, rating, and notes. No tag writes or file changes. Shortcuts: F favorite · M maybe · R reviewed · X reject · W needs work · N/P next/previous.
      </StatusStrip>
      <div className="beets-review-layout">
        <aside className="beets-review-candidates">
          {tracks.map((track) => (
            <button
              className="beets-review-option"
              key={track.track_id}
              onClick={() => { setSelected(track); setNotes(track.notes) }}
            >
              <span>
                <strong>{track.title || track.filename}</strong>
                <small>{track.artist || 'Unknown artist'} · {track.genre || 'Unknown genre'}</small>
              </span>
              <Badge tone="info">{track.review_status}</Badge>
            </button>
          ))}
        </aside>
        <section className="beets-review-detail">
          <AudioPreviewPlayer
            track={selected ? {
              id: selected.track_id,
              artist: selected.artist,
              title: selected.title,
              filename: selected.filename,
              genre: selected.genre,
              bpm: selected.bpm,
              key_camelot: selected.key_camelot,
              duration_sec: selected.duration_sec,
            } : null}
          />
          {selected && (
            <>
              <div className="settings-action-row">
                {['reviewed', 'favorite', 'maybe', 'rejected', 'needs_work', 'unreviewed'].map((status) => (
                  <button className="btn btn--ghost btn--sm" key={status} onClick={() => void save(status)}>
                    {status.replace(/_/g, ' ')}
                  </button>
                ))}
              </div>
              <label>
                Rating
                <select
                  className="form-input"
                  value={selected.rating ?? 0}
                  onChange={(event) => void apiFetch.patch(`/reviews/tracks/${selected.track_id}`, { rating: Number(event.target.value), notes })}
                >
                  <option value="0">No rating</option>
                  {[1, 2, 3, 4, 5].map((rating) => <option key={rating}>{rating}</option>)}
                </select>
              </label>
              <label>
                Notes
                <textarea className="form-input" value={notes} onChange={(event) => setNotes(event.target.value)} />
              </label>
              <button className="btn btn--primary" onClick={() => void save(selected.review_status)}>Save notes</button>
            </>
          )}
        </section>
      </div>
    </main>
  )
}
