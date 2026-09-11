import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import * as reviewsApi from '../../api/reviews'
import type { TrackSummary } from '../../types/track'
import BulkRatingFavoriteEditor from './BulkRatingFavoriteEditor'

vi.mock('../../api/reviews', () => ({ previewReviewSignals: vi.fn(), applyReviewSignals: vi.fn() }))

const track = (id: number, rating: number | null, favorite: boolean): TrackSummary => ({
  id, filepath: `/managed/Inbox/${id}.mp3`, filename: `${id}.mp3`, artist: 'Artist', title: `Title ${id}`,
  genre: 'House', comment: null, label: null, bpm: 122, key_camelot: '8A', key_musical: null,
  duration_sec: 180, bitrate_kbps: 320, status: 'pending', quality_tier: 'HIGH', parse_confidence: 'HIGH',
  storage_zone: 'INBOX', issues: [], rating, favorite, review_status: 'unreviewed',
})

describe('BulkRatingFavoriteEditor', () => {
  it('shows mixed state and explicit preview counts before apply', async () => {
    vi.mocked(reviewsApi.previewReviewSignals).mockResolvedValue({
      selected_count: 2, eligible_count: 2, changeable_count: 2, missing_count: 0,
      fields: {
        rating: { operation: 'set', value: 4, mixed: true, affected_count: 2, already_matching_count: 0, skipped_count: 0 },
        favorite: { operation: 'set', value: true, mixed: true, affected_count: 1, already_matching_count: 1, skipped_count: 0 },
      }, items: [], message: 'Preview only.',
    })
    render(<BulkRatingFavoriteEditor trackIds={[1, 2]} tracks={[track(1, 2, true), track(2, null, false)]} onApplied={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getAllByText('Current: Mixed')).toHaveLength(2)
    fireEvent.change(screen.getByLabelText('Rating'), { target: { value: 'set' } })
    fireEvent.change(screen.getByLabelText('Rating to set'), { target: { value: '4' } })
    fireEvent.change(screen.getByLabelText('Favorites'), { target: { value: 'add' } })
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))
    await waitFor(() => expect(reviewsApi.previewReviewSignals).toHaveBeenCalledWith([1, 2], {
      rating: { operation: 'set', value: 4 }, favorite: { operation: 'set', value: true },
    }))
    expect(screen.getByText('Set to 4 stars')).toBeInTheDocument()
    expect(screen.getByText('Add to Favorites').parentElement).toHaveTextContent('1 will change')
    expect(screen.getByText('Add to Favorites').parentElement).toHaveTextContent('1 already match')
    expect(reviewsApi.applyReviewSignals).not.toHaveBeenCalled()
  })
})
