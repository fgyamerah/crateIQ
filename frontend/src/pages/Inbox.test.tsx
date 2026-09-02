import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as workspaceApi from '../api/workspace'
import type { InboxPreparationState, InboxPreparationStatus, TrackSummary } from '../types/track'
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

function preparation(
  trackId: number,
  status: InboxPreparationStatus,
  reasons: string[] = [],
  warnings: string[] = [],
): InboxPreparationState {
  return {
    track_id: trackId,
    status,
    status_label: labels[status],
    reasons: reasons.map((label, index) => ({
      code: `reason_${index}`,
      label,
      severity: status === 'WRITE_BLOCKED' ? 'blocker' : status === 'UNSAVED' ? 'unsaved' : status === 'REVIEW' ? 'review' : 'attention',
    })),
    warnings: warnings.map((label, index) => ({ code: `warning_${index}`, label })),
    pending_fields: status === 'UNSAVED' ? ['artist'] : [],
    review_count: status === 'REVIEW' ? 1 : 0,
    write: {
      has_unsaved_changes: status === 'UNSAVED',
      blocked: status === 'WRITE_BLOCKED',
      blocker_code: status === 'WRITE_BLOCKED' ? 'unsupported_write_format' : null,
      last_failure: null,
    },
    promotion: { ready: status === 'READY', destination: null, collision: null },
  }
}

function track(id: number, state: InboxPreparationState): TrackSummary {
  return {
    id,
    filepath: `/managed/Inbox/track-${id}.mp3`,
    filename: `track-${id}.mp3`,
    artist: 'Artist',
    title: `Title ${id}`,
    genre: 'House',
    bpm: state.warnings.some((warning) => warning.label.includes('BPM')) ? null : 122,
    key_camelot: state.warnings.some((warning) => warning.label.includes('Key')) ? null : '8A',
    key_musical: null,
    duration_sec: 180,
    bitrate_kbps: 320,
    status: 'pending',
    quality_tier: 'HIGH',
    parse_confidence: 'HIGH',
    storage_zone: 'INBOX',
    issues: [],
    preparation_state: state,
  }
}

const statusCounts = {
  ALL: 5,
  WRITE_BLOCKED: 1,
  NEEDS_ATTENTION: 1,
  REVIEW: 1,
  UNSAVED: 1,
  READY: 1,
} as const

function page(items: TrackSummary[], total = items.length) {
  return {
    items,
    limit: 200,
    offset: 0,
    total,
    status_counts: statusCounts,
    available_track_ids: [1, 2, 3, 4, 5],
  }
}

describe('Inbox preparation status', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(workspaceApi.fetchWorkspaceStatus).mockResolvedValue({
      state: 'managed_workspace',
      library_root: '/managed',
      inbox_path: '/managed/Inbox',
      library_path: '/managed/Library',
      quarantine_path: '/managed/Quarantine',
      marker_version: 1,
      message: 'Managed workspace',
    })
    vi.mocked(workspaceApi.fetchPreparePreview).mockResolvedValue({
      library_root: '/managed', inbox_total: 5, already_ready: 1, need_cleaning: 0,
      need_enrichment: 0, need_analysis: 0, likely_review: 4,
      unsupported_write_format: 1, enrichment_lookup_bound: 10, message: 'Preview only',
    })
  })

  it('renders distinct human statuses, accessible reasons, secondary warnings, and unchanged actions', async () => {
    const states = [
      preparation(1, 'READY', [], ['BPM is missing', 'Key is missing']),
      preparation(2, 'UNSAVED', ['Artist has changes not yet written to file']),
      preparation(3, 'WRITE_BLOCKED', ['M4A metadata write-back is not supported']),
      preparation(4, 'NEEDS_ATTENTION', ['Genre is missing']),
      preparation(5, 'REVIEW', ['Metadata sources disagree on Genre']),
    ]
    vi.mocked(workspaceApi.fetchInboxTracks).mockResolvedValue(page(states.map((state) => track(state.track_id, state)), 5))
    vi.mocked(workspaceApi.previewPromotion).mockResolvedValue({
      library_root: '/managed', track_count: 5, ready_count: 1, blocked_count: 4,
      items: [], message: 'Preview only',
    })

    render(<MemoryRouter><Inbox /></MemoryRouter>)

    await waitFor(() => expect(workspaceApi.fetchInboxTracks).toHaveBeenCalled())
    const table = screen.getByRole('table')
    expect(within(table).getByText('Ready')).toBeInTheDocument()
    expect(within(table).getByText('Unsaved')).toBeInTheDocument()
    expect(within(table).getByText('Write Blocked')).toBeInTheDocument()
    expect(within(table).getByText('Needs Attention')).toBeInTheDocument()
    expect(within(table).getByText('Review')).toBeInTheDocument()

    expect(within(table).getByText('BPM is missing. Key is missing')).toBeInTheDocument()
    expect(within(table).getByText('Artist has changes not yet written to file')).toBeInTheDocument()
    expect(within(table).getByText('M4A metadata write-back is not supported')).toBeInTheDocument()
    expect(within(table).getByText('Genre is missing')).toBeInTheDocument()
    expect(within(table).getByText('Metadata sources disagree on Genre')).toBeInTheDocument()

    expect(screen.getByRole('button', { name: 'Process All' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clean Selected (0)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enrich Selected (0)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bulk Edit (0)' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open Needs Review' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Move Ready to Library (1)' })).toBeInTheDocument()
  })
})

describe('Inbox filtering, selection, and inspector', () => {
  const ready = preparation(1, 'READY', [], ['BPM is missing'])
  ready.promotion.destination = 'Library/House/Artist/Artist - Title 1.mp3'
  const attention = preparation(2, 'NEEDS_ATTENTION', ['Genre is missing'], ['Key is missing'])
  const blocked = preparation(3, 'WRITE_BLOCKED', ['M4A metadata write-back is not supported'])
  blocked.write.last_failure = 'The latest metadata write did not complete'
  const allTracks = [track(1, ready), track(2, attention), track(3, blocked)]

  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(workspaceApi.fetchWorkspaceStatus).mockResolvedValue({
      state: 'managed_workspace',
      library_root: '/managed',
      inbox_path: '/managed/Inbox',
      library_path: '/managed/Library',
      quarantine_path: '/managed/Quarantine',
      marker_version: 1,
      message: 'Managed workspace',
    })
    vi.mocked(workspaceApi.fetchPreparePreview).mockResolvedValue({
      library_root: '/managed', inbox_total: 3, already_ready: 1, need_cleaning: 0,
      need_enrichment: 0, need_analysis: 0, likely_review: 2,
      unsupported_write_format: 1, enrichment_lookup_bound: 10, message: 'Preview only',
    })
    vi.mocked(workspaceApi.previewPromotion).mockResolvedValue({
      library_root: '/managed', track_count: 3, ready_count: 1, blocked_count: 2,
      items: [], message: 'Preview only',
    })
    vi.mocked(workspaceApi.fetchInboxTracks).mockImplementation(async (params = {}) => {
      let items = allTracks
      if (params.preparation_status) {
        items = items.filter((item) => item.preparation_state?.status === params.preparation_status)
      }
      if (params.search) {
        const term = params.search.toLowerCase()
        items = items.filter((item) => [item.filename, item.artist, item.title, item.genre]
          .some((field) => field?.toLowerCase().includes(term)))
      }
      return page(items, items.length)
    })
    vi.mocked(workspaceApi.fetchInboxTrackInspection).mockImplementation(async (id) => {
      const item = allTracks.find((candidate) => candidate.id === id)
      if (!item) throw new Error('not found')
      return item
    })
  })

  it('selects preparation filters, renders full counts, and combines search with status', async () => {
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    await screen.findByRole('button', { name: 'Needs Attention 1' })

    fireEvent.click(screen.getByRole('button', { name: 'Needs Attention 1' }))
    await waitFor(() => expect(workspaceApi.fetchInboxTracks).toHaveBeenCalledWith(
      expect.objectContaining({ preparation_status: 'NEEDS_ATTENTION' }),
    ))
    expect(screen.getByRole('button', { name: 'Needs Attention 1' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search all Inbox tracks' }), { target: { value: 'Title 2' } })
    await waitFor(() => expect(workspaceApi.fetchInboxTracks).toHaveBeenCalledWith(
      expect.objectContaining({ search: 'Title 2', preparation_status: 'NEEDS_ATTENTION' }),
    ), { timeout: 1500 })
  })

  it('keeps ID selection across sort and explains hidden selections across filters', async () => {
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    const checkbox = await screen.findByRole('checkbox', { name: 'Select track-1.mp3' })
    fireEvent.click(checkbox)
    expect(screen.getByText('1 selected · 1 visible')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Artist' }))
    await waitFor(() => expect(workspaceApi.fetchInboxTracks).toHaveBeenCalledWith(
      expect.objectContaining({ sort: 'artist', order: 'desc' }),
    ))
    expect(screen.getByRole('checkbox', { name: 'Select track-1.mp3' })).toBeChecked()

    fireEvent.click(screen.getByRole('button', { name: 'Needs Attention 1' }))
    await screen.findByRole('checkbox', { name: 'Select track-2.mp3' })
    expect(screen.getByText('1 selected · 0 visible')).toBeInTheDocument()
    expect(screen.getByText('1 selected outside this page or filter')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear hidden' }))
    expect(screen.queryByText(/selected ·/)).not.toBeInTheDocument()
  })

  it('labels Select Visible semantics and clears the exact selected IDs', async () => {
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    const selectVisible = await screen.findByRole('checkbox', { name: 'Select visible page (3 tracks)' })
    fireEvent.click(selectVisible)
    expect(screen.getByText('3 selected · 3 visible')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clean Selected (3)' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear selection' }))
    expect(screen.getByRole('button', { name: 'Clean Selected (0)' })).toBeDisabled()
  })

  it('supports shift-click range selection within the rendered page', async () => {
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    const first = await screen.findByRole('checkbox', { name: 'Select track-1.mp3' })
    fireEvent.click(first)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select track-3.mp3' }), { shiftKey: true })
    expect(screen.getByText('3 selected · 3 visible')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Select track-2.mp3' })).toBeChecked()
  })

  it('opens a read-only inspector with reasons, warnings, write state, and destination', async () => {
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    const inspect = await screen.findByRole('button', { name: 'Inspect track-1.mp3' })
    fireEvent.click(inspect)

    const inspector = screen.getByRole('dialog', { name: 'Inbox Track Inspector' })
    expect(within(inspector).getByText('Ready')).toBeInTheDocument()
    expect(within(inspector).getAllByText('BPM is missing')).toHaveLength(2)
    expect(within(inspector).getByText('Library/House/Artist/Artist - Title 1.mp3')).toBeInTheDocument()

    fireEvent.click(within(inspector).getByRole('tab', { name: 'File' }))
    expect(within(inspector).getByText('/managed/Inbox/track-1.mp3')).toBeInTheDocument()
    expect(within(inspector).getByText('Available')).toBeInTheDocument()
  })

  it('does not open the inspector from a checkbox and closes it with Escape', async () => {
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    const checkbox = await screen.findByRole('checkbox', { name: 'Select track-1.mp3' })
    fireEvent.click(checkbox)
    expect(screen.queryByRole('dialog', { name: 'Inbox Track Inspector' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Inspect track-1.mp3' }))
    expect(screen.getByRole('dialog', { name: 'Inbox Track Inspector' })).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Inbox Track Inspector' })).not.toBeInTheDocument())
  })

  it('shows write blockers and unresolved write failures without implementation codes', async () => {
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect track-3.mp3' }))
    const inspector = screen.getByRole('dialog', { name: 'Inbox Track Inspector' })
    expect(within(inspector).getAllByText('M4A metadata write-back is not supported')).toHaveLength(3)
    expect(within(inspector).getByText('The latest metadata write did not complete')).toBeInTheDocument()
    expect(within(inspector).queryByText('unsupported_write_format')).not.toBeInTheDocument()
    fireEvent.click(within(inspector).getByRole('button', { name: 'Close Track Inspector' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Inbox Track Inspector' })).not.toBeInTheDocument())
  })

  it('keeps the established toolbar actions visible', async () => {
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    await screen.findByRole('button', { name: 'Process All' })
    expect(screen.getByRole('button', { name: 'Clean Selected (0)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enrich Selected (0)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bulk Edit (0)' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open Needs Review' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Move Ready to Library (1)' })).toBeInTheDocument()
  })
})
