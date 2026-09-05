import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as workspaceApi from '../api/workspace'
import * as metadataSourcesApi from '../api/metadataSources'
import * as enrichmentReviewApi from '../api/enrichmentReview'
import type { InboxPreparationState, InboxPreparationStatus, TrackSummary } from '../types/track'
import type { EnrichmentSuggestion } from '../types/enrichmentReview'
import Inbox from './Inbox'

vi.mock('../api/workspace', () => ({
  applyInboxBulkEdit: vi.fn(),
  applyPromotion: vi.fn(),
  cancelPrepareOperation: vi.fn(),
  cleanSelected: vi.fn(),
  enrichSelected: vi.fn(),
  fetchInboxTrackInspection: vi.fn(),
  fetchInboxTracks: vi.fn(),
  fetchPrepareOperation: vi.fn(),
  fetchPreparePreview: vi.fn(),
  fetchWorkspaceStatus: vi.fn(),
  importToInbox: vi.fn(),
  patchInboxTrack: vi.fn(),
  previewInboxBulkEdit: vi.fn(),
  previewPromotion: vi.fn(),
  startProcessAll: vi.fn(),
}))

vi.mock('../api/metadataSources', () => ({ fetchMetadataSources: vi.fn() }))

vi.mock('../api/enrichmentReview', () => ({
  applyEnrichmentSuggestion: vi.fn(),
  fetchInboxTrackEnrichmentReview: vi.fn(),
  updateEnrichmentSuggestion: vi.fn(),
}))

vi.mock('../hooks/useTrackWaveform', () => ({
  useTrackWaveform: () => ({
    waveform: { status: 'not_generated', trackId: 1, jobId: null, errorCode: null },
    loading: false,
    generating: false,
    generationUnavailable: false,
    actionError: null,
    generate: vi.fn(),
    cancel: vi.fn(),
  }),
}))

const labels: Record<InboxPreparationStatus, InboxPreparationState['status_label']> = {
  WRITE_BLOCKED: 'Write Blocked',
  NEEDS_ATTENTION: 'Needs Attention',
  REVIEW: 'Review',
  UNSAVED: 'Unsaved',
  READY: 'Ready',
}

function reviewState(trackId: number, reviewCount: number): InboxPreparationState {
  return {
    track_id: trackId,
    status: 'REVIEW',
    status_label: labels.REVIEW,
    reasons: [{ code: 'provider_review_genre', label: 'Suggested Genre needs review', severity: 'review' }],
    warnings: [],
    pending_fields: [],
    review_count: reviewCount,
    write: { has_unsaved_changes: false, blocked: false, blocker_code: null, last_failure: null },
    promotion: { ready: false, destination: null, collision: null },
  }
}

function track(id: number): TrackSummary {
  return {
    id,
    filepath: `/managed/Inbox/track-${id}.mp3`,
    filename: `track-${id}.mp3`,
    artist: 'Da Capo',
    title: `Title ${id}`,
    genre: 'Afro House',
    bpm: 122,
    key_camelot: '8A',
    key_musical: null,
    duration_sec: 180,
    bitrate_kbps: 320,
    status: 'pending',
    quality_tier: 'HIGH',
    parse_confidence: 'HIGH',
    storage_zone: 'INBOX',
    issues: [],
    preparation_state: reviewState(id, 1),
  }
}

function suggestion(): EnrichmentSuggestion {
  return {
    suggestion_id: 'sug-1',
    track_id: 1,
    source_id: 'beets',
    confidence: 'medium',
    reason: 'Provider consensus needs review: genre: non_authority_genre_evidence (MEDIUM).',
    filename: 'track-1.mp3',
    relative_path: 'Inbox/track-1.mp3',
    current_fields: { artist: 'Da Capo', title: 'Title 1', genre: 'Afro House' },
    suggested_fields: { genre: 'Afro Tech' },
    allowed_fields: ['genre'],
    decision: 'pending',
    note: '',
    selected_fields: {},
  }
}

function page(items: TrackSummary[], total = items.length) {
  return {
    items,
    limit: 200,
    offset: 0,
    total,
    status_counts: { ALL: total, WRITE_BLOCKED: 0, NEEDS_ATTENTION: 0, REVIEW: 1, UNSAVED: 0, READY: 0 },
    available_track_ids: items.map((item) => item.id),
  }
}


describe('Inbox inline enrichment review integration', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(metadataSourcesApi.fetchMetadataSources).mockResolvedValue({ sources: [] })
    vi.mocked(workspaceApi.fetchWorkspaceStatus).mockResolvedValue({
      state: 'managed_workspace', library_root: '/managed', inbox_path: '/managed/Inbox',
      library_path: '/managed/Library', quarantine_path: '/managed/Quarantine', marker_version: 1,
      message: 'Managed workspace',
    })
    vi.mocked(workspaceApi.fetchPreparePreview).mockResolvedValue({
      library_root: '/managed', inbox_total: 1, already_ready: 0, need_cleaning: 0,
      need_enrichment: 0, need_analysis: 0, likely_review: 1,
      unsupported_write_format: 0, enrichment_lookup_bound: 10, message: 'Preview only',
    })
    vi.mocked(workspaceApi.previewPromotion).mockResolvedValue({
      library_root: '/managed', track_count: 1, ready_count: 0, blocked_count: 1,
      items: [], message: 'Preview only',
    })
    vi.mocked(workspaceApi.fetchInboxTracks).mockResolvedValue(page([track(1)], 1))
    vi.mocked(workspaceApi.fetchInboxTrackInspection).mockResolvedValue(track(1))
  })

  it('exposes a Review tab, a per-track review count, and resolves without a tag write', async () => {
    const item = suggestion()
    const emptyReview = { summary: {}, items: [], sources: [], safety: [], warnings: [], latest_preview_at: null, message: null }
    vi.mocked(enrichmentReviewApi.fetchInboxTrackEnrichmentReview)
      .mockResolvedValueOnce({ track_id: 1, items: [item], count: 1, sources: [], safety: [], message: null })
      .mockResolvedValueOnce({ track_id: 1, items: [], count: 0, sources: [], safety: [], message: null })
    vi.mocked(enrichmentReviewApi.updateEnrichmentSuggestion).mockResolvedValue(emptyReview)
    vi.mocked(enrichmentReviewApi.applyEnrichmentSuggestion).mockResolvedValue({ applied: 1, skipped: 0, failed: 0, warnings: [], review: emptyReview })

    render(<MemoryRouter><Inbox /></MemoryRouter>)

    const table = await screen.findByRole('table')
    expect(within(table).getByText('1 suggestion')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Inspect track-1.mp3' }))
    const inspector = await screen.findByRole('dialog', { name: 'Inbox Track Inspector' })
    fireEvent.click(within(inspector).getByRole('tab', { name: 'Review' }))

    expect(await within(inspector).findByText('1 suggestion need review')).toBeInTheDocument()
    expect(within(inspector).getByText('Afro Tech')).toBeInTheDocument()

    const fetchCount = vi.mocked(workspaceApi.fetchInboxTracks).mock.calls.length
    fireEvent.click(within(inspector).getByRole('button', { name: 'Use Suggested (1)' }))

    await waitFor(() => expect(enrichmentReviewApi.applyEnrichmentSuggestion).toHaveBeenCalledWith(1, 'sug-1', { genre: 'Afro Tech' }))
    await waitFor(() => expect(vi.mocked(workspaceApi.fetchInboxTracks).mock.calls.length).toBeGreaterThan(fetchCount))
    // The review decision must not route through the metadata PATCH / tag-write path.
    expect(workspaceApi.patchInboxTrack).not.toHaveBeenCalled()
  })
})
