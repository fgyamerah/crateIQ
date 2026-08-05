import {
  ChevronDown,
  ChevronUp,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'
import ThreeBandWaveform from './ThreeBandWaveform'
import { usePersistentPlayer } from './usePersistentPlayer'

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
}

function displayTitle(title: string | null, filename: string | null): string {
  return title || filename || 'Untitled track'
}

export default function PersistentBottomPlayer() {
  const player = usePersistentPlayer()
  const track = player.currentTrack
  if (!track) return null

  const resolvedDuration = player.duration || track.duration_sec || 0
  const sourceDetail = track.relativePath || track.sourceLabel || track.filename || 'Local browser preview'

  return (
    <section
      className={`persistent-bottom-player${player.minimized ? ' persistent-bottom-player--minimized' : ''}${player.playing ? ' is-playing' : ''}`}
      aria-label="Persistent audio preview player"
    >
      <div className="persistent-player-track">
        <span className="persistent-player-art" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        <span className="persistent-player-copy">
          <strong title={displayTitle(track.title, track.filename)}>{displayTitle(track.title, track.filename)}</strong>
          <span title={track.artist || 'Unknown artist'}>{track.artist || 'Unknown artist'}</span>
          {!player.minimized && <small title={sourceDetail}>{sourceDetail}</small>}
        </span>
      </div>

      <div className="persistent-player-transport">
        <button
          type="button"
          className="persistent-player-icon-btn"
          onClick={player.previous}
          disabled={!player.canPrevious}
          aria-label="Previous track"
          title="Previous track"
        >
          <SkipBack size={17} fill="currentColor" />
        </button>
        <button
          type="button"
          className="persistent-player-play"
          onClick={() => void player.togglePlayback()}
          disabled={player.status === 'unavailable'}
          aria-label={player.playing ? 'Pause audio preview' : 'Play audio preview'}
        >
          {player.status === 'loading'
            ? <Loader2 className="spin" size={20} />
            : player.playing
              ? <Pause size={20} fill="currentColor" />
              : <Play size={20} fill="currentColor" />}
        </button>
        <button
          type="button"
          className="persistent-player-icon-btn"
          onClick={player.next}
          disabled={!player.canNext}
          aria-label="Next track"
          title="Next track"
        >
          <SkipForward size={17} fill="currentColor" />
        </button>
        <span className="persistent-player-queue-position" aria-label="Queue position">
          {player.currentIndex + 1} / {player.queue.length}
        </span>
      </div>

      {!player.minimized && (
        <div className="persistent-player-waveform">
          <ThreeBandWaveform seed={track.id} inactive={!player.playing} compact />
          <div className="persistent-player-timeline">
            <span>{formatTime(player.currentTime)}</span>
            <input
              type="range"
              min="0"
              max={resolvedDuration || 0}
              step="0.1"
              value={Math.min(player.currentTime, resolvedDuration || 0)}
              disabled={!resolvedDuration || player.status === 'unavailable'}
              onChange={(event) => player.seek(Number(event.target.value))}
              aria-label="Audio preview position"
            />
            <span>{formatTime(resolvedDuration)}</span>
          </div>
        </div>
      )}

      {!player.minimized && (
        <div className="persistent-player-utility">
          <div className={`persistent-player-state${player.status === 'unavailable' ? ' is-unavailable' : ''}`} aria-live="polite">
            {player.status === 'unavailable' ? (
              <>
                <span title={player.error ?? undefined}>{player.error}</span>
                <button type="button" onClick={player.retry}><RotateCcw size={12} /> Retry</button>
              </>
            ) : (
              <span>{player.status === 'loading' ? 'Loading browser preview…' : 'Browser preview · visual waveform only'}</span>
            )}
          </div>
          <label className="persistent-player-volume">
            {player.volume === 0 ? <VolumeX size={16} /> : <Volume2 size={16} />}
            <span className="sr-only">Preview volume</span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={player.volume}
              onChange={(event) => player.setVolume(Number(event.target.value))}
              aria-label="Preview volume"
            />
          </label>
        </div>
      )}

      <div className="persistent-player-window-controls">
        <button
          type="button"
          className="persistent-player-icon-btn"
          onClick={() => player.setMinimized(!player.minimized)}
          aria-label={player.minimized ? 'Expand player' : 'Minimize player'}
          title={player.minimized ? 'Expand player' : 'Minimize player'}
        >
          {player.minimized ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
        </button>
        <button
          type="button"
          className="persistent-player-icon-btn"
          onClick={player.close}
          aria-label="Close player"
          title="Close player"
        >
          <X size={16} />
        </button>
      </div>
    </section>
  )
}
