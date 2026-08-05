import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Play } from 'lucide-react'
import AudioPreviewPlayer from '../player/AudioPreviewPlayer'
import type { CompatibleTrack, CompatibleTracksResponse, TrackDetail } from '../../types/track'
import { fetchCompatibleTracks } from '../../api/tracks'
import { ApiError } from '../../api/client'
import CamelotWheel from './CamelotWheel'
import { camelotHeroStyle, camelotStyle, displayValue } from './libraryUtils'

function WaveformPreview({ seed, inactive }: { seed: number; inactive?: boolean }) {
  const bars = useMemo(() => {
    let s = seed || 1
    const rnd = () => {
      s = (s * 1103515245 + 12345) & 0x7fffffff
      return (s % 100) / 100
    }
    return Array.from({ length: 64 }, () => 0.12 + rnd() * 0.88)
  }, [seed])
  return (
    <div className={`lib-waveform${inactive ? ' lib-waveform--inactive' : ''}`} aria-hidden="true">
      {bars.map((h, i) => (
        <span key={i} style={{ height: `${Math.round(h * 100)}%` }} />
      ))}
    </div>
  )
}

interface Props {
  track: TrackDetail | null
  loading: boolean
}

function compatInitials(item: CompatibleTrack): string {
  const source = (item.artist || item.display_title || '?').trim()
  return source.slice(0, 2).toUpperCase() || '?'
}

function CompatibleTrackRow({ item }: { item: CompatibleTrack }) {
  return (
    <li className="lib-compat-row">
      <span className="lib-compat-avatar" style={camelotStyle(item.key_camelot)} aria-hidden="true">
        {compatInitials(item)}
      </span>
      <div className="lib-compat-main">
        <strong>{item.display_title}</strong>
        <span className="lib-compat-sub">
          {displayValue(item.artist)}
          {item.genre ? ` · ${item.genre}` : ''}
        </span>
      </div>
      <div className="lib-compat-meta">
        <span className="lib-mono">{displayValue(item.bpm)}</span>
        {item.key_camelot ? (
          <span className="lib-camelot-chip lib-camelot-chip--sm" style={camelotStyle(item.key_camelot)}>
            {item.key_camelot}
          </span>
        ) : (
          <span className="lib-muted">—</span>
        )}
      </div>
      <span className="lib-compat-reason" title={item.compatibility_reason}>
        {item.compatibility_reason}
      </span>
    </li>
  )
}

function useCompatibleTracks(track: TrackDetail | null) {
  const [compat, setCompat] = useState<CompatibleTracksResponse | null>(null)
  const [compatLoading, setCompatLoading] = useState(false)
  const [compatError, setCompatError] = useState<string | null>(null)
  const requestSeq = useRef(0)

  useEffect(() => {
    if (!track || !track.key_camelot) {
      setCompat(null)
      setCompatError(null)
      setCompatLoading(false)
      return
    }
    const requestId = ++requestSeq.current
    setCompatLoading(true)
    setCompatError(null)
    fetchCompatibleTracks(track.id)
      .then((data) => {
        if (requestSeq.current !== requestId) return // a newer selection superseded this response
        setCompat(data)
      })
      .catch((e) => {
        if (requestSeq.current !== requestId) return
        setCompat(null)
        setCompatError(e instanceof ApiError ? e.displayMessage : 'Failed to load compatible tracks.')
      })
      .finally(() => {
        if (requestSeq.current === requestId) setCompatLoading(false)
      })
  }, [track?.id, track?.key_camelot])

  return { compat, compatLoading, compatError }
}

function CompatibleTracksSection({
  track,
  compat,
  compatLoading,
  compatError,
}: {
  track: TrackDetail | null
  compat: CompatibleTracksResponse | null
  compatLoading: boolean
  compatError: string | null
}) {
  let body: ReactNode
  if (!track) {
    body = <span className="lib-muted">Select a track to see harmonic matches.</span>
  } else if (!track.key_camelot) {
    body = <div className="lib-compat-empty">Add key/Camelot data to find compatible tracks.</div>
  } else if (compatLoading) {
    body = (
      <div className="lib-compat-loading" aria-hidden="true">
        {[0, 1, 2].map((i) => <div key={i} className="lib-compat-skeleton" />)}
      </div>
    )
  } else if (compatError) {
    body = <div className="lib-compat-error">Couldn't load compatible tracks — {compatError}</div>
  } else if (!compat || compat.items.length === 0) {
    body = <div className="lib-compat-empty">No compatible tracks found with current matching rules.</div>
  } else {
    body = (
      <ul className="lib-compat-list">
        {compat.items.map((item) => <CompatibleTrackRow key={item.id} item={item} />)}
      </ul>
    )
  }

  return (
    <section className="lib-inspector-section">
      <h3>Compatible tracks</h3>
      {body}
    </section>
  )
}

/**
 * Always renders the full inspector panel structure — hero, stat tiles,
 * metadata, parse confidence, issues, waveform, compatible-tracks note —
 * whether or not a track is selected. The empty/loading states dim the same
 * layout rather than swapping in a bare "select a track" box, so the right
 * rail never collapses to an empty panel.
 */
export default function TrackInspector({ track, loading }: Props) {
  const heroTitle = loading ? 'Loading…' : (track ? (track.title || track.filename) : 'Select a track')
  const heroSubtitle = loading ? '' : (track ? (track.artist || '(no artist)') : 'Nothing selected yet')
  const placeholder = !track
  const { compat, compatLoading, compatError } = useCompatibleTracks(track)
  const compatCamelotKeys = useMemo(
    () => (compat?.items ?? []).map((item) => item.key_camelot).filter((k): k is string => !!k),
    [compat],
  )

  return (
    <aside className={`lib-card lib-inspector${placeholder ? ' lib-inspector--placeholder' : ''}`}>
      <div className="lib-inspector-kicker">
        <strong>Track inspector</strong>
        <span>Preview and harmonic context</span>
      </div>
      <div className="lib-inspector-hero">
        <button
          type="button"
          className="lib-inspector-art"
          disabled
          title={track ? 'Audio preview controls are below' : 'Select a track to preview it'}
          style={track ? camelotHeroStyle(track.key_camelot) : undefined}
        >
          <Play size={20} fill="currentColor" />
        </button>
        <div className="lib-inspector-hero-copy">
          <strong>{heroTitle}</strong>
          {heroSubtitle && <span>{heroSubtitle}</span>}
          {track?.genre && <span className="lib-tag">{track.genre}</span>}
        </div>
      </div>

      <div className="lib-inspector-player">
        <AudioPreviewPlayer track={track} />
      </div>

      <div className="lib-inspector-stats">
        <div className="lib-stat-tile">
          <strong>{track ? displayValue(track.bpm) : '—'}</strong>
          <span>BPM</span>
        </div>
        <div className="lib-stat-tile" style={track ? camelotStyle(track.key_camelot) : undefined}>
          <strong style={{ color: 'inherit' }}>{track ? displayValue(track.key_musical) : '—'}</strong>
          <span>Key</span>
        </div>
        <div className="lib-stat-tile" style={track ? camelotStyle(track.key_camelot) : undefined}>
          <strong style={{ color: 'inherit' }}>{track ? displayValue(track.key_camelot) : '—'}</strong>
          <span>Camelot</span>
        </div>
      </div>

      <section className="lib-inspector-section lib-inspector-section--harmonic">
        <div className="lib-inspector-section-heading">
          <h3>Harmonic mixing</h3>
          <span className="lib-harmonic-key">{track?.key_camelot ?? 'No key'}</span>
        </div>
        <div className="lib-harmonic-wheel-frame">
          <CamelotWheel camelot={track?.key_camelot ?? null} compatibleKeys={compatCamelotKeys} />
        </div>
        <span className="lib-muted lib-inspector-note">
          The wheel highlights the selected key and safe adjacent or relative matches.
        </span>
      </section>

      <section className="lib-inspector-section">
        <h3>Metadata</h3>
        <dl className="lib-defs">
          <dt>Artist</dt><dd>{track ? displayValue(track.artist) : '—'}</dd>
          <dt>Title</dt><dd>{track ? displayValue(track.title) : '—'}</dd>
          <dt>Genre</dt><dd>{track ? displayValue(track.genre) : '—'}</dd>
          <dt>Bitrate</dt><dd>{track?.bitrate_kbps ? `${track.bitrate_kbps} kbps` : '—'}</dd>
          <dt>File</dt><dd className="lib-defs-path">{track ? displayValue(track.filesystem_path || track.filepath) : '—'}</dd>
        </dl>
      </section>

      <section className="lib-inspector-section">
        <h3>Parse Confidence</h3>
        <span className={`lib-conf-chip lib-conf-chip--${(track?.parse_confidence || 'unknown').toLowerCase()}`}>
          {track ? (track.parse_confidence || 'UNKNOWN') : '—'}
        </span>
      </section>

      <section className="lib-inspector-section">
        <h3>Issues</h3>
        <div className="lib-badge-row">
          {!track && <span className="lib-muted">Select a track to see its issue flags</span>}
          {track && (track.issues.length
            ? track.issues.map((issue) => <span key={issue} className="lib-issue-badge">{issue.replace(/_/g, ' ')}</span>)
            : <span className="lib-muted">No current issue flags</span>)}
        </div>
      </section>

      <section className="lib-inspector-section">
        <h3>Signal preview</h3>
        <WaveformPreview seed={track?.id ?? 0} inactive={!track} />
        <span className="lib-muted lib-inspector-note">
          Visual placeholder only — no waveform analysis is performed.
        </span>
      </section>

      <CompatibleTracksSection
        track={track}
        compat={compat}
        compatLoading={compatLoading}
        compatError={compatError}
      />
    </aside>
  )
}
