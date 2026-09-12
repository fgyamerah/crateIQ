import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Playlists from './Playlists'
import AddToPlaylistDialog from '../components/playlists/AddToPlaylistDialog'
import * as playlistApi from '../api/userPlaylists'
import * as reviewsApi from '../api/reviews'
import * as tracksApi from '../api/tracks'
import * as playerApi from '../components/player/usePersistentPlayer'

vi.mock('../api/userPlaylists', () => ({
  fetchUserPlaylists: vi.fn(),
  fetchUserPlaylist: vi.fn(),
  createUserPlaylist: vi.fn(),
  updateUserPlaylist: vi.fn(),
  deleteUserPlaylist: vi.fn(),
  removeFromPlaylist: vi.fn(),
  removeSelectedFromPlaylist: vi.fn(),
  reorderPlaylist: vi.fn(),
  previewAddToPlaylist: vi.fn(),
  addToPlaylist: vi.fn(),
}))
vi.mock('../api/reviews', () => ({ updateTrackReview: vi.fn() }))
vi.mock('../api/tracks', () => ({ fetchTrack: vi.fn() }))
vi.mock('../components/player/usePersistentPlayer', () => ({ usePersistentPlayer: vi.fn() }))
vi.mock('../components/library/TrackInspector', () => ({
  default: ({ onAddToPlaylist }: { onAddToPlaylist?: () => void }) => <aside aria-label="Track Inspector"><button type="button" onClick={onAddToPlaylist}>Add to Playlist</button></aside>,
}))

const first = {
  track_id: 1, position: 1, added_at: '2026-09-12T00:00:00Z', artist: 'Alpha', title: 'First', filename: 'alpha.mp3', filepath: '/library/alpha.mp3', genre: 'House', comment: null, label: null, bpm: 120, key_musical: 'A minor', key_camelot: '8A', duration_sec: 180, bitrate_kbps: 320, status: 'ok', quality_tier: 'HIGH', parse_confidence: 'HIGH', storage_zone: 'LIBRARY', rating: 5, favorite: true, missing_from_library: false,
}
const second = { ...first, track_id: 2, position: 2, artist: 'Beta', title: 'Second', filename: 'beta.mp3', rating: null, favorite: false }
const summary = { id: 1, name: 'Warm Up', description: 'First hour', created_at: '2026-09-11T00:00:00Z', updated_at: '2026-09-12T00:00:00Z', track_count: 2 }
const detail = { ...summary, tracks: [first, second] }

const player = {
  currentTrack: null, sourceUrl: null, queue: [], currentIndex: -1, status: 'idle' as const, error: null,
  playing: false, currentTime: 0, duration: 0, volume: 0.8, minimized: false, canPrevious: false, canNext: false,
  loadTrack: vi.fn(), togglePlayback: vi.fn().mockResolvedValue(undefined), play: vi.fn(), pause: vi.fn(), seek: vi.fn(), setVolume: vi.fn(), previous: vi.fn(), next: vi.fn(), retry: vi.fn(), close: vi.fn(), setMinimized: vi.fn(),
}

describe('Playlists', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(playerApi.usePersistentPlayer).mockReturnValue(player)
    vi.mocked(playlistApi.fetchUserPlaylists).mockResolvedValue([summary])
    vi.mocked(playlistApi.fetchUserPlaylist).mockResolvedValue(detail)
    vi.mocked(tracksApi.fetchTrack).mockResolvedValue({ ...first, id: first.track_id, issues: [], filesize_bytes: 1, filesystem_path: first.filepath, error_msg: null, processed_at: null, pipeline_ver: null } as never)
    vi.mocked(reviewsApi.updateTrackReview).mockResolvedValue({ track_id: 1, review_status: 'unreviewed', rating: 4, favorite: true })
  })

  it('renders an empty state and creates a playlist from the index', async () => {
    vi.mocked(playlistApi.fetchUserPlaylists).mockResolvedValue([])
    vi.mocked(playlistApi.createUserPlaylist).mockResolvedValue({ ...summary, track_count: 0 })
    render(<MemoryRouter initialEntries={['/playlists']}><Playlists /></MemoryRouter>)

    expect(await screen.findByText('No playlists yet')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Create a playlist' }))
    fireEvent.change(screen.getByPlaceholderText('e.g. Warm Up'), { target: { value: 'Warm Up' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create playlist' }))
    await waitFor(() => expect(playlistApi.createUserPlaylist).toHaveBeenCalledWith({ name: 'Warm Up', description: null }))
  })

  it('shows default manual order, search, signals, and remove controls in detail', async () => {
    render(<MemoryRouter initialEntries={['/playlists/1']}><Routes><Route path="/playlists/:playlistId" element={<Playlists />} /></Routes></MemoryRouter>)

    expect(await screen.findByText('Alpha — First')).toBeInTheDocument()
    const rows = screen.getAllByRole('row')
    expect(within(rows[1]).getByText('Alpha — First')).toBeInTheDocument()
    expect(within(rows[2]).getByText('Beta — Second')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('Search artist, title, filename…'), { target: { value: 'Beta' } })
    expect(screen.queryByText('Alpha — First')).not.toBeInTheDocument()
    expect(screen.getByText('Beta — Second')).toBeInTheDocument()
    expect(screen.getByLabelText('Favorites only')).toBeInTheDocument()
  })

  it('requires a bulk add preview and reports already-present tracks', async () => {
    vi.mocked(playlistApi.previewAddToPlaylist).mockResolvedValue({ playlist_id: 1, selected_count: 2, will_add_count: 1, already_present_count: 1, missing_count: 0, track_ids: [1, 2], message: '1 will be added · 1 already present' })
    vi.mocked(playlistApi.addToPlaylist).mockResolvedValue({ playlist: detail, selected_count: 2, added_count: 1, already_present_count: 1, missing_count: 0, message: '1 added · 1 already present' })
    render(<AddToPlaylistDialog trackIds={[1, 2]} onClose={vi.fn()} />)

    await screen.findByRole('combobox')
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }))
    expect(await screen.findByText('1 will be added')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    await waitFor(() => expect(playlistApi.addToPlaylist).toHaveBeenCalledWith(1, [1, 2], true))
    expect(await screen.findByText('1 added · 1 already present')).toBeInTheDocument()
  })
})
