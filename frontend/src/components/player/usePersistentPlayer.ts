import { createContext, useContext } from 'react'
import type { AudioPreviewTrack } from './AudioPreviewPlayer'

export interface PersistentPlayerTrack extends AudioPreviewTrack {
  relativePath?: string | null
  sourceLabel?: string | null
}

export type PersistentPlayerStatus = 'idle' | 'loading' | 'ready' | 'unavailable'

export interface LoadTrackOptions {
  autoplay?: boolean
}

export interface PersistentPlayerContextValue {
  currentTrack: PersistentPlayerTrack | null
  sourceUrl: string | null
  queue: PersistentPlayerTrack[]
  currentIndex: number
  status: PersistentPlayerStatus
  error: string | null
  playing: boolean
  currentTime: number
  duration: number
  volume: number
  minimized: boolean
  canPrevious: boolean
  canNext: boolean
  loadTrack: (
    track: PersistentPlayerTrack,
    queue?: PersistentPlayerTrack[],
    options?: LoadTrackOptions,
  ) => void
  togglePlayback: () => Promise<void>
  play: () => Promise<void>
  pause: () => void
  seek: (seconds: number) => void
  setVolume: (volume: number) => void
  previous: () => void
  next: () => void
  retry: () => void
  close: () => void
  setMinimized: (minimized: boolean) => void
}

export const PersistentPlayerContext = createContext<PersistentPlayerContextValue | null>(null)

export function usePersistentPlayer(): PersistentPlayerContextValue {
  const context = useContext(PersistentPlayerContext)
  if (!context) {
    throw new Error('usePersistentPlayer must be used within PersistentPlayerProvider')
  }
  return context
}
