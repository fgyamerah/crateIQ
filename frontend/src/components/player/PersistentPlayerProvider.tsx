import { useCallback, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { previewAudioUrl } from '../../api/audio'
import {
  PersistentPlayerContext,
  type LoadTrackOptions,
  type PersistentPlayerContextValue,
  type PersistentPlayerStatus,
  type PersistentPlayerTrack,
} from './usePersistentPlayer'

const PLAYBACK_ERROR = 'Browser playback is unavailable for this track. The file may be missing, outside the selected library, or unsupported by this browser.'

function uniqueQueue(queue: PersistentPlayerTrack[]): PersistentPlayerTrack[] {
  const seen = new Set<number>()
  return queue.filter((track) => {
    if (seen.has(track.id)) return false
    seen.add(track.id)
    return true
  })
}

export default function PersistentPlayerProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const autoplayRef = useRef(false)
  const [currentTrack, setCurrentTrack] = useState<PersistentPlayerTrack | null>(null)
  const [queue, setQueue] = useState<PersistentPlayerTrack[]>([])
  const [currentIndex, setCurrentIndex] = useState(-1)
  const [status, setStatus] = useState<PersistentPlayerStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolumeState] = useState(0.8)
  const [minimized, setMinimized] = useState(false)

  const markUnavailable = useCallback(() => {
    autoplayRef.current = false
    setStatus('unavailable')
    setError(PLAYBACK_ERROR)
    setPlaying(false)
  }, [])

  const play = useCallback(async () => {
    const audio = audioRef.current
    if (!audio || !currentTrack) return
    try {
      setError(null)
      await audio.play()
    } catch {
      markUnavailable()
    }
  }, [currentTrack, markUnavailable])

  const pause = useCallback(() => {
    audioRef.current?.pause()
  }, [])

  const loadTrack = useCallback((
    track: PersistentPlayerTrack,
    requestedQueue: PersistentPlayerTrack[] = [track],
    options: LoadTrackOptions = {},
  ) => {
    const nextQueue = uniqueQueue(requestedQueue.length ? requestedQueue : [track])
    let nextIndex = nextQueue.findIndex((item) => item.id === track.id)
    if (nextIndex === -1) {
      nextQueue.unshift(track)
      nextIndex = 0
    }

    setQueue(nextQueue)
    setCurrentIndex(nextIndex)
    setCurrentTrack(track)
    setMinimized(false)
    setError(null)

    if (currentTrack?.id === track.id) {
      if (options.autoplay) void play()
      return
    }

    autoplayRef.current = Boolean(options.autoplay)
    setStatus('loading')
    setPlaying(false)
    setCurrentTime(0)
    setDuration(0)
  }, [currentTrack?.id, play])

  const moveToIndex = useCallback((nextIndex: number, autoplay: boolean) => {
    const track = queue[nextIndex]
    if (!track) return
    autoplayRef.current = autoplay
    setCurrentIndex(nextIndex)
    setCurrentTrack(track)
    setStatus('loading')
    setError(null)
    setPlaying(false)
    setCurrentTime(0)
    setDuration(0)
  }, [queue])

  const previous = useCallback(() => {
    if (currentIndex <= 0) return
    moveToIndex(currentIndex - 1, playing)
  }, [currentIndex, moveToIndex, playing])

  const next = useCallback(() => {
    if (currentIndex < 0 || currentIndex >= queue.length - 1) return
    moveToIndex(currentIndex + 1, playing)
  }, [currentIndex, moveToIndex, playing, queue.length])

  const togglePlayback = useCallback(async () => {
    if (playing) pause()
    else await play()
  }, [pause, play, playing])

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current
    if (!audio || !Number.isFinite(seconds)) return
    const upperBound = Number.isFinite(audio.duration) ? audio.duration : Math.max(0, seconds)
    const nextTime = Math.max(0, Math.min(seconds, upperBound))
    audio.currentTime = nextTime
    setCurrentTime(nextTime)
  }, [])

  const setVolume = useCallback((nextVolume: number) => {
    const normalized = Math.max(0, Math.min(1, nextVolume))
    setVolumeState(normalized)
    if (audioRef.current) audioRef.current.volume = normalized
  }, [])

  const retry = useCallback(() => {
    const audio = audioRef.current
    if (!audio || !currentTrack) return
    autoplayRef.current = false
    setStatus('loading')
    setError(null)
    audio.load()
  }, [currentTrack])

  const close = useCallback(() => {
    const audio = audioRef.current
    autoplayRef.current = false
    audio?.pause()
    if (audio) {
      audio.removeAttribute('src')
      audio.load()
    }
    setCurrentTrack(null)
    setQueue([])
    setCurrentIndex(-1)
    setStatus('idle')
    setError(null)
    setPlaying(false)
    setCurrentTime(0)
    setDuration(0)
    setMinimized(false)
  }, [])

  const sourceUrl = currentTrack ? previewAudioUrl(currentTrack.id) : null
  const value = useMemo<PersistentPlayerContextValue>(() => ({
    currentTrack,
    sourceUrl,
    queue,
    currentIndex,
    status,
    error,
    playing,
    currentTime,
    duration,
    volume,
    minimized,
    canPrevious: currentIndex > 0,
    canNext: currentIndex >= 0 && currentIndex < queue.length - 1,
    loadTrack,
    togglePlayback,
    play,
    pause,
    seek,
    setVolume,
    previous,
    next,
    retry,
    close,
    setMinimized,
  }), [
    close, currentIndex, currentTime, currentTrack, duration, error, loadTrack,
    minimized, next, pause, play, playing, previous, queue, retry, seek,
    setVolume, sourceUrl, status, togglePlayback, volume,
  ])

  return (
    <PersistentPlayerContext.Provider value={value}>
      {children}
      <audio
        ref={audioRef}
        src={sourceUrl ?? undefined}
        preload="metadata"
        onLoadStart={() => {
          if (currentTrack) setStatus('loading')
        }}
        onCanPlay={() => {
          setStatus('ready')
          setError(null)
          if (autoplayRef.current) {
            autoplayRef.current = false
            void play()
          }
        }}
        onLoadedMetadata={(event) => {
          const nextDuration = event.currentTarget.duration
          setDuration(Number.isFinite(nextDuration) ? nextDuration : 0)
        }}
        onDurationChange={(event) => {
          const nextDuration = event.currentTarget.duration
          if (Number.isFinite(nextDuration)) setDuration(nextDuration)
        }}
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false)
          if (currentIndex >= 0 && currentIndex < queue.length - 1) {
            moveToIndex(currentIndex + 1, true)
          }
        }}
        onError={markUnavailable}
      />
    </PersistentPlayerContext.Provider>
  )
}
