import { useMemo } from 'react'
import { Play } from 'lucide-react'
import type { TrackDetail } from '../../types/track'
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

  return (
    <aside className={`lib-card lib-inspector${placeholder ? ' lib-inspector--placeholder' : ''}`}>
      <div className="lib-inspector-hero">
        <button
          type="button"
          className="lib-inspector-art"
          disabled
          title={track ? 'Playback is not implemented yet' : 'Select a track to preview it'}
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
        <h3>Waveform preview</h3>
        <WaveformPreview seed={track?.id ?? 0} inactive={!track} />
        <span className="lib-muted lib-inspector-note">
          Visual placeholder only — no audio preview/waveform data source is wired up yet.
        </span>
      </section>

      <section className="lib-inspector-section">
        <h3>Compatible tracks</h3>
        <div className="lib-deferred-note">
          Compatible-tracks matching coming soon. Harmonic scoring exists for Set Builder, but is
          not yet exposed as a per-track lookup API for the Library inspector.
        </div>
      </section>
    </aside>
  )
}
