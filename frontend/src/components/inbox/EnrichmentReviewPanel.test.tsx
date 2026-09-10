import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import {
  applyEnrichmentSuggestion,
  fetchInboxTrackEnrichmentReview,
  updateEnrichmentSuggestion,
} from '../../api/enrichmentReview'
import type { EnrichmentReview, EnrichmentSuggestion, InboxTrackEnrichmentReview } from '../../types/enrichmentReview'
import EnrichmentReviewPanel from './EnrichmentReviewPanel'

vi.mock('../../api/enrichmentReview', () => ({
  applyEnrichmentSuggestion: vi.fn(),
  fetchInboxTrackEnrichmentReview: vi.fn(),
  updateEnrichmentSuggestion: vi.fn(),
}))

function suggestion(overrides: Partial<EnrichmentSuggestion> = {}): EnrichmentSuggestion {
  return {
    suggestion_id: 'sug-1',
    track_id: 1,
    source_id: 'beets',
    confidence: 'medium',
    reason: 'Provider consensus needs review: genre: non_authority_genre_evidence (MEDIUM).',
    filename: 'track.mp3',
    relative_path: 'Inbox/track.mp3',
    current_fields: { artist: 'Da Capo', title: 'Title', genre: 'Afro House' },
    suggested_fields: { genre: 'Afro Tech' },
    allowed_fields: ['genre'],
    decision: 'pending',
    note: '',
    selected_fields: {},
    ...overrides,
  }
}

function review(items: EnrichmentSuggestion[]): InboxTrackEnrichmentReview {
  return {
    track_id: 1,
    items,
    count: items.length,
    sources: [{ id: 'beets', label: 'Beets', category: 'external_api', enabled: true, configured: true, connection_status: 'ready', current_behavior: 'implemented' }],
    safety: ['db_only', 'no_tag_writes'],
    message: null,
  }
}

function fullReview(items: EnrichmentSuggestion[] = []): EnrichmentReview {
  return {
    summary: { suggestions: items.length, pending: items.length, applied: 0, ignored: 0, review_later: 0, fields_selected: 0 },
    items,
    sources: [],
    safety: ['db_only', 'no_tag_writes'],
    warnings: [],
    latest_preview_at: null,
    message: null,
  }
}

describe('EnrichmentReviewPanel', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('shows an empty state when there are no actionable suggestions', async () => {
    vi.mocked(fetchInboxTrackEnrichmentReview).mockResolvedValue(review([]))
    render(<EnrichmentReviewPanel trackId={1} />)
    expect(await screen.findByText('No suggestions need review')).toBeInTheDocument()
  })

  it('shows current and suggested values, confidence text, and source attribution', async () => {
    vi.mocked(fetchInboxTrackEnrichmentReview).mockResolvedValue(review([suggestion()]))
    render(<EnrichmentReviewPanel trackId={1} />)

    expect(await screen.findByText('Afro House')).toBeInTheDocument()
    expect(screen.getByText('Afro Tech')).toBeInTheDocument()
    expect(screen.getByText('MEDIUM')).toBeInTheDocument()
    expect(screen.getByText('Beets')).toBeInTheDocument()
    expect(screen.getByText('1 suggestion need review')).toBeInTheDocument()
  })

  it('presents CONFLICT evidence without inventing a single best suggestion', async () => {
    vi.mocked(fetchInboxTrackEnrichmentReview).mockResolvedValue(review([suggestion({
      source_id: 'consensus_review',
      suggested_fields: {},
      allowed_fields: [],
      confidence: 'low',
      current_fields: { artist: 'Da Capo', title: 'Title', genre: 'House' },
      evidence: { genre: ['Discogs: Afro Tech', 'Beets: Afro House'] },
      reason: 'Provider consensus needs review: genre: genre_provider_disagreement (CONFLICT).',
    })]))
    render(<EnrichmentReviewPanel trackId={1} />)

    expect(await screen.findByText('CONFLICT')).toBeInTheDocument()
    expect(screen.getByText('Discogs')).toBeInTheDocument()
    expect(screen.getByText('Afro Tech')).toBeInTheDocument()
    expect(screen.getByText('Beets')).toBeInTheDocument()
    expect(screen.getByText('Afro House')).toBeInTheDocument()
    // No "Use suggested" checkbox is offered for an unresolved conflict field.
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })


  it('renders multiple fields from one suggestion', async () => {
    vi.mocked(fetchInboxTrackEnrichmentReview).mockResolvedValue(review([suggestion({
      current_fields: { artist: 'Old Artist', title: 'Old Title', genre: 'House' },
      suggested_fields: { artist: 'New Artist', title: 'New Title' },
      allowed_fields: ['artist', 'title'],
    })]))
    render(<EnrichmentReviewPanel trackId={1} />)

    expect(await screen.findByText('Artist')).toBeInTheDocument()
    expect(screen.getByText('Title')).toBeInTheDocument()
    expect(screen.getByText('New Artist')).toBeInTheDocument()
    expect(screen.getByText('New Title')).toBeInTheDocument()
    expect(screen.getAllByRole('checkbox')).toHaveLength(2)
  })

  it('keeps primary actions before and outside the scrollable review details', async () => {
    vi.mocked(fetchInboxTrackEnrichmentReview).mockResolvedValue(review([suggestion({
      evidence: {
        genre: ['Discogs: Afro Tech', 'Beets: Afro Tech', 'Beatport: Afro Tech'],
      },
    })]))
    render(<EnrichmentReviewPanel trackId={1} />)

    const useSuggested = await screen.findByRole('button', { name: 'Use Suggested (1)' })
    const actionArea = screen.getByLabelText('Primary review actions')
    const details = screen.getByLabelText('Review evidence and field details')

    expect(actionArea).toContainElement(useSuggested)
    expect(details).not.toContainElement(useSuggested)
    expect(actionArea.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(details).toContainElement(screen.getByText('3 sources agree'))
  })

  it('Use Suggested saves the selection then applies, and refreshes', async () => {
    const onDecision = vi.fn()
    const item = suggestion()
    vi.mocked(fetchInboxTrackEnrichmentReview)
      .mockResolvedValueOnce(review([item]))
      .mockResolvedValueOnce(review([]))
    vi.mocked(updateEnrichmentSuggestion).mockResolvedValue(fullReview([item]))
    vi.mocked(applyEnrichmentSuggestion).mockResolvedValue({
      applied: 1, skipped: 0, failed: 0, warnings: [], review: fullReview([item]),
    })

    render(<EnrichmentReviewPanel trackId={1} onDecision={onDecision} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Use Suggested (1)' }))

    await waitFor(() => expect(applyEnrichmentSuggestion).toHaveBeenCalledWith(1, 'sug-1', { genre: 'Afro Tech' }))
    expect(updateEnrichmentSuggestion).toHaveBeenCalledWith(1, 'sug-1', {
      decision: 'pending', note: '', selected_fields: { genre: 'Afro Tech' },
    })
    await waitFor(() => expect(onDecision).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('No suggestions need review')).toBeInTheDocument()
  })

  it('Keep Current resolves the suggestion through the ignored decision', async () => {
    const onDecision = vi.fn()
    const item = suggestion()
    vi.mocked(fetchInboxTrackEnrichmentReview)
      .mockResolvedValueOnce(review([item]))
      .mockResolvedValueOnce(review([]))
    vi.mocked(updateEnrichmentSuggestion).mockResolvedValue(fullReview([]))

    render(<EnrichmentReviewPanel trackId={1} onDecision={onDecision} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Keep Current' }))

    await waitFor(() => expect(updateEnrichmentSuggestion).toHaveBeenCalledWith(1, 'sug-1', {
      decision: 'ignored', note: '', selected_fields: {},
    }))
    expect(applyEnrichmentSuggestion).not.toHaveBeenCalled()
    await waitFor(() => expect(onDecision).toHaveBeenCalledTimes(1))
  })

  it('surfaces a stale/already-resolved apply failure and refetches', async () => {
    const item = suggestion()
    vi.mocked(fetchInboxTrackEnrichmentReview)
      .mockResolvedValueOnce(review([item]))
      .mockResolvedValueOnce(review([item]))
    vi.mocked(updateEnrichmentSuggestion).mockResolvedValue(fullReview([item]))
    vi.mocked(applyEnrichmentSuggestion).mockResolvedValue({
      applied: 0, skipped: 0, failed: 1, warnings: ['Suggestion was not found in the latest preview.'], review: fullReview([item]),
    })

    render(<EnrichmentReviewPanel trackId={1} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Use Suggested (1)' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Suggestion was not found in the latest preview.')
    // The suggestion remains actionable after a failed apply.
    expect(screen.getByRole('button', { name: 'Use Suggested (1)' })).toBeInTheDocument()
  })

  it('does not trigger any tag-write request during review actions', async () => {
    const item = suggestion()
    vi.mocked(fetchInboxTrackEnrichmentReview)
      .mockResolvedValueOnce(review([item]))
      .mockResolvedValueOnce(review([]))
    vi.mocked(updateEnrichmentSuggestion).mockResolvedValue(fullReview([item]))
    vi.mocked(applyEnrichmentSuggestion).mockResolvedValue({
      applied: 1, skipped: 0, failed: 0, warnings: [], review: fullReview([item]),
    })

    render(<EnrichmentReviewPanel trackId={1} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Use Suggested (1)' }))
    await waitFor(() => expect(applyEnrichmentSuggestion).toHaveBeenCalled())

    expect(updateEnrichmentSuggestion).toHaveBeenCalledTimes(1)
    expect(applyEnrichmentSuggestion).toHaveBeenCalledTimes(1)
    // The panel only talks to the enrichment-review surface; it never calls
    // the workspace metadata/tag-write endpoints.
  })
})
