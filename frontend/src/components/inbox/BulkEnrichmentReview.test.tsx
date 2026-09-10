import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as enrichmentApi from '../../api/enrichmentReview'
import type { BulkEnrichmentSummary } from '../../types/enrichmentReview'
import BulkEnrichmentReview from './BulkEnrichmentReview'

vi.mock('../../api/enrichmentReview', () => ({
  acceptSafeEnrichmentSuggestions: vi.fn(),
  fetchBulkEnrichmentSummary: vi.fn(),
  keepCurrentBulkEnrichment: vi.fn(),
}))

const summary: BulkEnrichmentSummary = {
  selected_count: 3,
  safe_count: 1,
  exception_count: 1,
  no_suggestion_count: 1,
  message: 'Safe means existing HIGH-confidence additions only.',
  rows: [
    { track_id: 1, filename: 'safe.mp3', artist: 'A', title: 'Safe', genre: null, review_state: 'safe', confidence: 'HIGH', conflicts: [], suggestion_count: 1, reason: 'HIGH-confidence additions only' },
    { track_id: 2, filename: 'conflict.mp3', artist: 'B', title: 'Conflict', genre: 'House', review_state: 'exception', confidence: 'CONFLICT', conflicts: ['artist'], suggestion_count: 1, reason: 'Providers disagree' },
    { track_id: 3, filename: 'none.mp3', artist: 'C', title: 'None', genre: 'Techno', review_state: 'no_suggestion', confidence: null, conflicts: [], suggestion_count: 0, reason: 'No useful pending suggestion' },
  ],
}

describe('BulkEnrichmentReview', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(enrichmentApi.fetchBulkEnrichmentSummary).mockResolvedValue(summary)
  })

  it('shows selected, safe, exception, and no-suggestion counts with triage rows', async () => {
    render(<BulkEnrichmentReview trackIds={[1, 2, 3]} refreshKey={0} onReviewExceptions={vi.fn()} onResolved={vi.fn()} onClose={vi.fn()} />)
    const region = await screen.findByRole('region', { name: 'Bulk enrichment review' })
    expect(within(region).getByText('3 selected')).toBeInTheDocument()
    expect(within(region).getAllByText('1', { selector: 'b' })).toHaveLength(3)
    expect(within(region).getByText('safe.mp3')).toBeInTheDocument()
    expect(within(region).getByText('Providers disagree')).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Accept 1 Safe Suggestion' })).toBeEnabled()
  })

  it('accepts only through the safe endpoint after confirmation and leaves exceptions in the returned summary', async () => {
    const next = { ...summary, safe_count: 0, rows: summary.rows.map((row) => row.track_id === 1 ? { ...row, review_state: 'no_suggestion' as const, suggestion_count: 0 } : row) }
    vi.mocked(enrichmentApi.acceptSafeEnrichmentSuggestions).mockResolvedValue({ selected_count: 3, applied: 1, skipped: 0, failed: 0, summary: next })
    const onResolved = vi.fn()
    render(<BulkEnrichmentReview trackIds={[1, 2, 3]} refreshKey={0} onReviewExceptions={vi.fn()} onResolved={onResolved} onClose={vi.fn()} />)
    const accept = await screen.findByRole('button', { name: 'Accept 1 Safe Suggestion' })
    fireEvent.click(accept)
    expect(enrichmentApi.acceptSafeEnrichmentSuggestions).not.toHaveBeenCalled()
    let confirm = screen.getByRole('button', { name: 'Confirm safe acceptance' })
    await waitFor(() => expect(confirm).toHaveFocus())
    expect(screen.getByRole('button', { name: 'Keep Current for 3' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(accept).toHaveFocus())
    fireEvent.click(accept)
    confirm = screen.getByRole('button', { name: 'Confirm safe acceptance' })
    fireEvent.click(confirm)
    await waitFor(() => expect(enrichmentApi.acceptSafeEnrichmentSuggestions).toHaveBeenCalledWith([1, 2, 3]))
    expect(onResolved).toHaveBeenCalledWith(next)
    await waitFor(() => expect(screen.getByLabelText('Bulk enrichment result')).toHaveFocus())
    expect(screen.getByText('Providers disagree')).toBeInTheDocument()
  })

  it('opens exactly the exception queue and keeps current only after confirmation', async () => {
    const onReview = vi.fn()
    vi.mocked(enrichmentApi.keepCurrentBulkEnrichment).mockResolvedValue({
      selected_count: 3, kept_track_count: 2, suggestions_ignored: 2,
      summary: { ...summary, safe_count: 0, exception_count: 0, no_suggestion_count: 3, rows: summary.rows.map((row) => ({ ...row, review_state: 'no_suggestion', suggestion_count: 0 })) },
    })
    render(<BulkEnrichmentReview trackIds={[1, 2, 3]} refreshKey={0} onReviewExceptions={onReview} onResolved={vi.fn()} onClose={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Review 1 Exception' }))
    expect(onReview).toHaveBeenCalledWith([2])
    fireEvent.click(screen.getByRole('button', { name: 'Keep Current for 3' }))
    expect(enrichmentApi.keepCurrentBulkEnrichment).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm keep current' }))
    await waitFor(() => expect(enrichmentApi.keepCurrentBulkEnrichment).toHaveBeenCalledWith([1, 2, 3]))
  })

  it('reports skipped and failed safe suggestions as attention items', async () => {
    vi.mocked(enrichmentApi.acceptSafeEnrichmentSuggestions).mockResolvedValue({
      selected_count: 3, applied: 0, skipped: 1, failed: 1,
      warnings: ['One suggestion became stale.'],
      results: [
        { track_id: 1, suggestion_id: 'safe', status: 'skipped', reason: 'Existing metadata is now present.' },
        { track_id: 2, suggestion_id: 'stale', status: 'failed', reason: 'Suggestion was not found.' },
      ],
      summary: { ...summary, safe_count: 0 },
    })
    render(<BulkEnrichmentReview trackIds={[1, 2, 3]} refreshKey={0} onReviewExceptions={vi.fn()} onResolved={vi.fn()} onClose={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Accept 1 Safe Suggestion' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm safe acceptance' }))

    const attention = await screen.findByRole('list', { name: 'Enrichment suggestions requiring attention' })
    expect(attention).toHaveTextContent('Track 1')
    expect(attention).toHaveTextContent('Existing metadata is now present.')
    expect(attention).toHaveTextContent('Track 2')
    expect(screen.getByText(/1 skipped · 1 failed/)).toBeInTheDocument()
  })
})
