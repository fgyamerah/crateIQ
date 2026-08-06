import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { AudioWaveform, Loader2, Pause, Play } from 'lucide-react'
import ThreeBandWaveform from '../player/ThreeBandWaveform'
import TrackWaveform from '../player/TrackWaveform'
import { presentWaveformState } from '../player/waveformGeometry'
import { usePersistentPlayer } from '../player/usePersistentPlayer'
import { useTrackWaveform } from '../../hooks/useTrackWaveform'
import type { CompatibleTrack, CompatibleTracksResponse, TrackDetail } from '../../types/track'
import { fetchCompatibleTracks } from '../../api/tracks'
import { ApiError } from '../../api/client'
import CamelotWheel from './CamelotWheel'
import { camelotHeroStyle, camelotStyle, displayValue } from './libraryUtils'

interface Props {
  track: TrackDetail | null
  loading: boolean
  isCurrentTrack: boolean
  isPlaying: boolean
  onPlay: () => void
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
export default function TrackInspector({ track, loading, isCurrentTrack, isPlaying, onPlay }: Props) {
  const heroTitle = loading ? 'Loading…' : (track ? (track.title || track.filename) : 'Select a track')
  const heroSubtitle = loading ? '' : (track ? (track.artist || '(no artist)') : 'Nothing selected yet')
  const placeholder = !track
  const { compat, compatLoading, compatError } = useCompatibleTracks(track)
  const compatCamelotKeys = useMemo(
    () => (compat?.items ?? []).map((item) => item.key_camelot).filter((k): k is string => !!k),
    [compat],
  )

  // Read-only: the waveform GET runs automatically, generation never does.
  // Reuses the same lifecycle hook as the persistent player so the inspector
  // never owns a second source of waveform truth.
  const waveform = useTrackWaveform(track?.id ?? null)
  const persistentPlayer = usePersistentPlayer()
  const waveformState = track ? waveform.waveform : null
  const isWaveformReady = waveformState?.status === 'ready'
  const presentation = presentWaveformState(
    (track && (waveform.loading || !waveformState)) ? 'loading' : (waveformState?.status ?? 'not_generated'),
    !waveform.generationUnavailable,
  )
  const waveformMessage = waveform.actionError ?? presentation.message
  const showWaveformState = Boolean(track) && (Boolean(waveformMessage) || presentation.canGenerate || presentation.canCancel)
  const inspectorDuration = track ? (isCurrentTrack ? (persistentPlayer.duration || track.duration_sec || 0) : (track.duration_sec || 0)) : 0
  const inspectorCurrentTime = isCurrentTrack ? persistentPlayer.currentTime : 0

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
          disabled={!track || loading}
          onClick={onPlay}
          aria-label={isPlaying ? 'Pause persistent player' : 'Play selected track in persistent player'}
          title={track ? (isPlaying ? 'Pause persistent player' : 'Play in persistent player') : 'Select a track to preview it'}
          style={track ? camelotHeroStyle(track.key_camelot) : undefined}
        >
          {isPlaying ? <Pause size={20} fill="currentColor" /> : <Play size={20} fill="currentColor" />}
        </button>
        <div className="lib-inspector-hero-copy">
          <strong>{heroTitle}</strong>
          {heroSubtitle && <span>{heroSubtitle}</span>}
          {track?.genre && <span className="lib-tag">{track.genre}</span>}
        </div>
      </div>

      <div className="lib-inspector-player">
        <button
          type="button"
          className="lib-btn lib-btn--primary lib-btn--sm lib-inspector-player-action"
          disabled={!track || loading}
          onClick={onPlay}
        >
          {isPlaying ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}
          {!track ? 'Select a track to play' : isPlaying ? 'Pause player' : isCurrentTrack ? 'Play current track' : 'Play in bottom player'}
        </button>
        <span>Browser preview only · no tags or audio files are changed</span>
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
        <h3>Waveform</h3>
        <div className="lib-inspector-waveform">
          {track && isWaveformReady && waveformState?.status === 'ready' ? (
            <TrackWaveform
              peaks={waveformState.peaks}
              scale={waveformState.scale}
              currentTime={inspectorCurrentTime}
              duration={inspectorDuration}
              inactive={!(isCurrentTrack && isPlaying)}
            />
          ) : (
            <ThreeBandWaveform seed={track?.id ?? 0} inactive={!track} compact />
          )}
        </div>
        {showWaveformState && (
          <div
            className={`persistent-player-wave-state${presentation.degraded || waveform.actionError ? ' is-degraded' : ''}`}
            aria-live="polite"
          >
            {presentation.busy && <Loader2 className="spin" size={11} aria-hidden="true" />}
            <span title={waveformMessage}>{waveformMessage}</span>
            {presentation.canGenerate && (
              <button
                type="button"
                className="persistent-player-wave-action"
                onClick={waveform.generate}
                disabled={waveform.generating}
                aria-label="Generate waveform for this track"
              >
                <AudioWaveform size={11} aria-hidden="true" />
                Generate waveform
              </button>
            )}
            {presentation.canCancel && (
              <button
                type="button"
                className="persistent-player-wave-action"
                onClick={waveform.cancel}
                aria-label="Cancel waveform generation"
              >
                Cancel
              </button>
            )}
          </div>
        )}
        {!track && (
          <span className="lib-muted lib-inspector-note">Select a track to preview its waveform.</span>
        )}
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
