import { useEffect, useRef, useState } from 'react'
import { Loader2, Pause, Play, Volume2, VolumeX } from 'lucide-react'
import { previewAudioUrl } from '../../api/audio'
import Badge from '../ui/Badge'
import EmptyState from '../ui/EmptyState'
import StatusStrip from '../ui/StatusStrip'

export interface AudioPreviewTrack {
  id: number
  artist: string | null
  title: string | null
  filename: string | null
  genre: string | null
  bpm: number | null
  key_camelot: string | null
  duration_sec: number | null
}

type PlayerState = 'ready' | 'loading' | 'unavailable'

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
}

function displayTitle(track: AudioPreviewTrack): string {
  return [track.artist, track.title].filter(Boolean).join(' — ') || track.filename || 'Untitled track'
}

export default function AudioPreviewPlayer({ track }: { track: AudioPreviewTrack | null }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [state, setState] = useState<PlayerState>('ready')
  const [playing, setPlaying] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(0.8)

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    audio.pause()
    setPlaying(false)
    setElapsed(0)
    setDuration(0)
    setState(track ? 'loading' : 'ready')
    if (track) audio.load()
  }, [track?.id])

  if (!track) {
    return <EmptyState title="Preview a track" message="Choose Preview on a library, Manual Crate, or Smart Crate track to load the native audio player." />
  }

  const playable = state !== 'unavailable'
  const resolvedDuration = duration || track.duration_sec || 0
  const togglePlayback = async () => {
    const audio = audioRef.current
    if (!audio || !playable) return
    try {
      if (audio.paused) await audio.play()
      else audio.pause()
    } catch {
      setState('unavailable')
      setPlaying(false)
    }
  }
  const seek = (value: number) => {
    const audio = audioRef.current
    if (!audio || !resolvedDuration) return
    audio.currentTime = value
    setElapsed(value)
  }
  const setPlayerVolume = (value: number) => {
    const audio = audioRef.current
    setVolume(value)
    if (audio) audio.volume = value
  }

  return (
    <section className={`audio-preview-player${playing ? ' audio-preview-player--playing' : ''}`} aria-label="Audio preview player">
      <audio
        ref={audioRef}
        src={previewAudioUrl(track.id)}
        preload="metadata"
        onLoadStart={() => setState('loading')}
        onCanPlay={() => setState('ready')}
        onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
        onTimeUpdate={(event) => setElapsed(event.currentTarget.currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onError={() => { setState('unavailable'); setPlaying(false) }}
      />
      <div className="audio-preview-main">
        <button className="btn btn--primary btn--sm audio-preview-toggle" type="button" onClick={() => void togglePlayback()} disabled={!playable} aria-label={playing ? `Pause ${displayTitle(track)}` : `Play ${displayTitle(track)}`}>
          {state === 'loading' ? <Loader2 className="spin" size={15} /> : playing ? <Pause size={15} /> : <Play size={15} />}
          {playing ? 'Pause' : 'Preview'}
        </button>
        <div className="audio-preview-track"><strong>{displayTitle(track)}</strong><span>{track.genre ?? 'Unknown genre'} · {track.bpm?.toFixed(1) ?? '—'} BPM</span></div>
        {track.key_camelot && <Badge tone="info">{track.key_camelot}</Badge>}
      </div>
      {state === 'unavailable' ? <StatusStrip tone="warn">Preview unavailable. The file may be missing, outside the selected library, or unsupported by this browser.</StatusStrip> : <div className="audio-preview-controls">
        <span className="audio-preview-time">{formatTime(elapsed)}</span>
        <input aria-label="Preview position" className="audio-preview-seek" type="range" min="0" max={resolvedDuration || 0} step="0.1" value={Math.min(elapsed, resolvedDuration || 0)} disabled={!resolvedDuration} onChange={(event) => seek(Number(event.target.value))} />
        <span className="audio-preview-time">{formatTime(resolvedDuration)}</span>
        <label className="audio-preview-volume">{volume === 0 ? <VolumeX size={15} /> : <Volume2 size={15} />}<span className="sr-only">Volume</span><input aria-label="Preview volume" type="range" min="0" max="1" step="0.05" value={volume} onChange={(event) => setPlayerVolume(Number(event.target.value))} /></label>
      </div>}
    </section>
  )
}
