import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TrackSummary } from '../../types/track'
import TrackTable from './TrackTable'

const track: TrackSummary = {
  id: 7,
  filepath: '/managed/Library/House/Artist/Artist - Track.mp3',
  filename: 'Artist - Track.mp3',
  artist: 'Artist',
  title: 'Track',
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
  rating: null,
  favorite: false,
}

function StatefulTable() {
  const [rating, setRating] = useState<number | null>(null)
  return (
    <TrackTable
      items={[{ ...track, rating }]}
      total={1}
      loading={false}
      offset={0}
      selectedId={null}
      sort="artist"
      order="asc"
      density="comfortable"
      playingTrackId={null}
      onSort={vi.fn()}
      onSelect={vi.fn()}
      onPlay={vi.fn()}
      onPrevPage={vi.fn()}
      onNextPage={vi.fn()}
      onOpenImportWizard={vi.fn()}
      onReviewChange={async (_trackId, patch) => setRating(patch.rating ?? null)}
    />
  )
}

describe('TrackTable rating controls', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    })
  })

  it('updates selected and aria-pressed star state immediately, including clearing', async () => {
    render(<MemoryRouter><StatefulTable /></MemoryRouter>)

    fireEvent.click(screen.getByRole('button', { name: 'Rate 4 stars' }))
    const clear = await screen.findByRole('button', { name: 'Clear rating' })
    await waitFor(() => expect(clear).toHaveAttribute('aria-pressed', 'true'))
    expect(clear.querySelector('svg')).toHaveAttribute('fill', 'currentColor')

    fireEvent.click(clear)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Rate 4 stars' })).toHaveAttribute('aria-pressed', 'false'))
    expect(screen.getByRole('button', { name: 'Rate 4 stars' }).querySelector('svg')).toHaveAttribute('fill', 'none')
  })
})
