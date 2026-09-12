import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as libraryApi from '../../api/library'
import * as reviewsApi from '../../api/reviews'
import * as tracksApi from '../../api/tracks'
import * as playerApi from '../player/usePersistentPlayer'
import LibraryView from './LibraryView'
import type { TrackSummary } from '../../types/track'

vi.mock('../../api/library', () => ({ fetchLibraryOverview: vi.fn() }))
vi.mock('../../api/reviews', () => ({ updateTrackReview: vi.fn() }))
vi.mock('../../api/tracks', () => ({ fetchTrack: vi.fn(), fetchTrackIssues: vi.fn(), fetchTrackPage: vi.fn() }))
vi.mock('../player/usePersistentPlayer', () => ({ usePersistentPlayer: vi.fn() }))
vi.mock('./TrackInspector', () => ({ default: () => <aside aria-label="Track Inspector" /> }))

const promoted: TrackSummary = {
  id: 1,
  filepath: '/managed/Library/House/Promoted/Promoted - Track.mp3',
  filename: 'Promoted - Track.mp3',
  artist: 'Promoted',
  title: 'Promoted Track',
  genre: 'House',
  bpm: 124,
  key_camelot: '8A',
  key_musical: 'A minor',
  duration_sec: 180,
  bitrate_kbps: 320,
  status: 'ok',
  quality_tier: 'HIGH',
  parse_confidence: 'HIGH',
  storage_zone: 'LIBRARY',
  issues: [],
  rating: 5,
  favorite: true,
}

const inbox: TrackSummary = {
  ...promoted,
  id: 2,
  filepath: '/managed/Inbox/inbox-track.mp3',
  filename: 'inbox-track.mp3',
  artist: 'Inbox',
  title: 'Inbox Track',
  storage_zone: 'INBOX',
  rating: null,
}

const overview = {
  total_tracks: 2,
  tracks_with_bpm: 2,
  tracks_with_camelot_key: 2,
  tracks_analyzed: 2,
  tracks_missing_artist: 0,
  tracks_missing_title: 0,
  parse_confidence_breakdown: { HIGH: 2 },
  genre_top_counts: [{ genre: 'House', count: 2 }],
}

const player = {
  currentTrack: null,
  sourceUrl: null,
  queue: [],
  currentIndex: -1,
  status: 'idle' as const,
  error: null,
  playing: false,
  currentTime: 0,
  duration: 0,
  volume: 0.8,
  minimized: false,
  canPrevious: false,
  canNext: false,
  loadTrack: vi.fn(),
  togglePlayback: vi.fn().mockResolvedValue(undefined),
  play: vi.fn().mockResolvedValue(undefined),
  pause: vi.fn(),
  seek: vi.fn(),
  setVolume: vi.fn(),
  previous: vi.fn(),
  next: vi.fn(),
  retry: vi.fn(),
  close: vi.fn(),
  setMinimized: vi.fn(),
}

describe('LibraryView Favorites projection', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    })
    vi.mocked(libraryApi.fetchLibraryOverview).mockResolvedValue(overview)
    vi.mocked(tracksApi.fetchTrackIssues).mockResolvedValue({
      missing_artist: 0, missing_title: 0, weak_filename_parse: 0,
      suspicious_artist: 0, suspicious_title: 0,
    })
    vi.mocked(tracksApi.fetchTrack).mockResolvedValue({ ...promoted, filesize_bytes: 0, filesystem_path: promoted.filepath, error_msg: null, processed_at: null, pipeline_ver: null } as never)
    vi.mocked(playerApi.usePersistentPlayer).mockReturnValue(player)
  })

  it('requests all-zone Favorites, shows promoted and Inbox favorites once, and removes only an unfavorited Inbox row', async () => {
    let inboxFavorite = true
    vi.mocked(tracksApi.fetchTrackPage).mockImplementation(async () => {
      const items = inboxFavorite ? [promoted, inbox] : [promoted]
      return { items, limit: 50, offset: 0, total: items.length }
    })
    vi.mocked(reviewsApi.updateTrackReview).mockImplementation(async (trackId, patch) => {
      if (trackId === inbox.id && patch.favorite === false) inboxFavorite = false
      return { track_id: trackId, review_status: 'unreviewed', rating: null, favorite: false }
    })

    render(<MemoryRouter><LibraryView favoriteOnly /></MemoryRouter>)

    expect(await screen.findByText('Promoted Track')).toBeInTheDocument()
    expect(screen.getByText('Inbox Track')).toBeInTheDocument()
    expect(screen.getAllByText('Inbox Track')).toHaveLength(1)
    await waitFor(() => expect(tracksApi.fetchTrackPage).toHaveBeenCalledWith(expect.objectContaining({ favorite_only: true })))
    const requestParams = vi.mocked(tracksApi.fetchTrackPage).mock.calls[0][0]
    expect(requestParams).toHaveProperty('zone', 'all')

    const inboxRow = screen.getByText('Inbox Track').closest('tr')
    expect(inboxRow).not.toBeNull()
    fireEvent.click(within(inboxRow as HTMLElement).getByRole('button', { name: 'Remove from Favorites' }))

    await waitFor(() => expect(reviewsApi.updateTrackReview).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.queryByText('Inbox Track')).not.toBeInTheDocument())
    expect(screen.getByText('Promoted Track')).toBeInTheDocument()
  })
})
