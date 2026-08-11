import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { fetchNeedsReview } from '../api/needsReview'
import NeedsReview from './NeedsReview'

vi.mock('../api/needsReview', () => ({ fetchNeedsReview: vi.fn() }))

const emptyResponse = {
  items: [],
  counts: { ALL: 0, METADATA: 0, IDENTITY_ENRICHMENT: 0, GENRE: 0, ANALYSIS: 0, QUALITY: 0 },
  message: 'No open review items.',
}

describe('Needs Review category selection', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(fetchNeedsReview).mockResolvedValue(emptyResponse)
  })

  it('selects a category and reloads that category', async () => {
    render(<MemoryRouter><NeedsReview /></MemoryRouter>)

    await waitFor(() => expect(fetchNeedsReview).toHaveBeenCalledWith('ALL'))
    const metadata = screen.getByRole('tab', { name: /metadata/i })
    fireEvent.click(metadata)

    expect(metadata).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /^all/i })).toHaveAttribute('aria-selected', 'false')
    await waitFor(() => expect(fetchNeedsReview).toHaveBeenLastCalledWith('METADATA'))
  })
})
