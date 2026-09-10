import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as workspaceApi from '../api/workspace'
import * as metadataSourcesApi from '../api/metadataSources'
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

vi.mock('../api/metadataSources', () => ({
  fetchMetadataSources: vi.fn(),
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

const statusCounts: Record<InboxPreparationStatus | 'ALL', number> = {
  ALL: 5,
  WRITE_BLOCKED: 1,
  NEEDS_ATTENTION: 1,
  REVIEW: 1,
  UNSAVED: 1,
  READY: 1,
}

function page(items: TrackSummary[], total = items.length, counts = statusCounts) {
  return {
    items,
    limit: 200,
    offset: 0,
    total,
    status_counts: counts,
    available_track_ids: [1, 2, 3, 4, 5],
  }
}

function metadataSource(id: string, displayName: string, overrides: Partial<Awaited<ReturnType<typeof metadataSourcesApi.fetchMetadataSources>>['sources'][number]> = {}) {
  return {
    id,
    label: displayName,
    category: 'external_api' as const,
    role: 'track_enrichment' as const,
    enabled: true,
    configured: true,
    needs_setup: false,
    selectable_for_enrichment: true,
    requires_credentials: false,
    credentials_status: 'not_required' as const,
    credential_fields: [],
    saved_credential_fields: [],
    connection_status: 'ready' as const,
    priority: 40,
    best_for: [],
    current_behavior: 'implemented' as const,
    configuration_note: null,
    safety: [],
    ...overrides,
  }
}

describe('Inbox preparation status', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(metadataSourcesApi.fetchMetadataSources).mockResolvedValue({ sources: [] })
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
    expect(screen.getByRole('button', { name: 'Bulk Review (0)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit Metadata (0)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save to File (0)' })).toBeDisabled()
    expect(screen.getByRole('link', { name: 'Open Needs Review' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Move Ready to Library (1)' })).toBeInTheDocument()
  })

  it('shows Save to File only for selected tracks with current writable changes', async () => {
    const unsaved = preparation(1, 'UNSAVED', ['Artist has changes not yet written to file'])
    vi.mocked(workspaceApi.fetchInboxTracks).mockResolvedValue(page([track(1, unsaved)], 1, {
      ALL: 1, WRITE_BLOCKED: 0, NEEDS_ATTENTION: 0, REVIEW: 0, UNSAVED: 1, READY: 0,
    }))
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select track-1.mp3' }))
    expect(screen.getByRole('button', { name: 'Save to File (1)' })).toBeEnabled()
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
    vi.mocked(metadataSourcesApi.fetchMetadataSources).mockResolvedValue({ sources: [
      metadataSource('musicbrainz', 'MusicBrainz'),
      metadataSource('deezer', 'Deezer', { priority: 80 }),
      metadataSource('discogs', 'Discogs', { enabled: false, selectable_for_enrichment: false }),
      metadataSource('local_tags', 'Local tags', { role: 'local_input', selectable_for_enrichment: false }),
    ] })
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
    expect(screen.getByRole('button', { name: 'Bulk Review (0)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit Metadata (0)' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open Needs Review' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Move Ready to Library (1)' })).toBeInTheDocument()
  })

  it('opens the source selector without enriching, shows only eligible sources, and preserves selection on cancel', async () => {
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select track-1.mp3' }))
    fireEvent.click(screen.getByRole('button', { name: 'Enrich Selected (1)' }))

    const dialog = await screen.findByRole('dialog', { name: /Find metadata for 1 selected track/ })
    expect(within(dialog).getByRole('checkbox', { name: 'MusicBrainz' })).toBeChecked()
    expect(within(dialog).getByRole('checkbox', { name: 'Deezer' })).toBeChecked()
    expect(within(dialog).queryByText('Discogs')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('Local tags')).not.toBeInTheDocument()
    expect(within(dialog).getByText('2 sources selected')).toBeInTheDocument()
    expect(workspaceApi.enrichSelected).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /Find metadata/ })).not.toBeInTheDocument())
    expect(screen.getByText('1 selected · 1 visible')).toBeInTheDocument()
  })

  it('sends the checked source IDs only after Find Metadata is confirmed', async () => {
    vi.mocked(workspaceApi.enrichSelected).mockResolvedValue({ enriched_count: 0, considered: 1, warnings: [] })
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select track-1.mp3' }))
    fireEvent.click(screen.getByRole('button', { name: 'Enrich Selected (1)' }))

    const dialog = await screen.findByRole('dialog', { name: /Find metadata for 1 selected track/ })
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Deezer' }))
    expect(within(dialog).getByText('1 source selected')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Find Metadata' }))

    await waitFor(() => expect(workspaceApi.enrichSelected).toHaveBeenCalledWith([1], ['musicbrainz']))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /Find metadata/ })).not.toBeInTheDocument())
    expect(screen.getByText('1 selected · 1 visible')).toBeInTheDocument()
  })

  it('disables Find Metadata and explains when no eligible sources are available', async () => {
    vi.mocked(metadataSourcesApi.fetchMetadataSources).mockResolvedValue({ sources: [] })
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select track-1.mp3' }))
    fireEvent.click(screen.getByRole('button', { name: 'Enrich Selected (1)' }))

    const dialog = await screen.findByRole('dialog', { name: /Find metadata for 1 selected track/ })
    expect(within(dialog).getByText('No sources available')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Find Metadata' })).toBeDisabled()
  })

  it('edits Artist, Title, Genre, and Album through the DB-first API and keeps selection', async () => {
    let working = allTracks[0]
    let fetchCount = 0
    vi.mocked(workspaceApi.fetchInboxTracks).mockImplementation(async () => {
      fetchCount += 1
      return page(fetchCount === 1 ? allTracks : [working, ...allTracks.slice(1)], 3)
    })
    vi.mocked(workspaceApi.patchInboxTrack).mockImplementation(async (id, fields) => {
      working = { ...working, ...fields, preparation_state: preparation(1, 'UNSAVED', ['Working metadata differs from file tags']) }
      return {
        track_id: id,
        rename: null,
        metadata: { ...working, album: working.album ?? null, preparation_state: working.preparation_state!, track_id: id, status: 'updated', fields_changed: Object.keys(fields), tag_write: null },
        errors: [],
      }
    })

    render(<MemoryRouter><Inbox /></MemoryRouter>)
    const select = await screen.findByRole('checkbox', { name: 'Select track-1.mp3' })
    fireEvent.click(select)

    for (const [field, nextValue] of [['Artist', 'New Artist'], ['Title', 'New Title'], ['Genre', 'Techno']] as const) {
      fireEvent.click(screen.getByRole('button', { name: `Edit ${field} for track-1.mp3` }))
      const input = screen.getByRole('textbox', { name: `${field} for track-1.mp3 value` })
      fireEvent.change(input, { target: { value: nextValue } })
      fireEvent.keyDown(input, { key: 'Enter' })
      await waitFor(() => expect(workspaceApi.patchInboxTrack).toHaveBeenCalledWith(1, { [field.toLowerCase()]: nextValue }))
      await waitFor(() => expect(screen.queryByRole('textbox', { name: `${field} for track-1.mp3 value` })).not.toBeInTheDocument())
    }

    expect(screen.getByText('New Artist')).toBeInTheDocument()
    expect(screen.getByText('New Title')).toBeInTheDocument()
    expect(screen.getByText('Techno')).toBeInTheDocument()
    expect(screen.getAllByText('Unsaved').length).toBeGreaterThan(0)
    expect(screen.getByText('1 selected · 1 visible')).toBeInTheDocument()
    expect(screen.getByText('track-1')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Edit Managed filename for track-1.mp3' }))
    const filenameInput = screen.getByRole('textbox', { name: 'Managed filename for track-1.mp3 value' })
    fireEvent.change(filenameInput, { target: { value: 'renamed-track-1' } })
    vi.mocked(workspaceApi.patchInboxTrack).mockResolvedValueOnce({
      track_id: 1,
      rename: { track_id: 1, status: 'renamed', filename: 'renamed-track-1.mp3', filepath: '/managed/Inbox/renamed-track-1.mp3' },
      metadata: null,
      errors: [],
    })
    fireEvent.keyDown(filenameInput, { key: 'Enter' })
    await waitFor(() => expect(workspaceApi.patchInboxTrack).toHaveBeenLastCalledWith(1, { filename: 'renamed-track-1' }))
    expect(screen.getByText('New Title')).toBeInTheDocument()
  })

  it('shows local validation and restores the prior value after a failed edit', async () => {
    vi.mocked(workspaceApi.patchInboxTrack).mockRejectedValue(new Error('Metadata service unavailable'))
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    const select = await screen.findByRole('checkbox', { name: 'Select track-1.mp3' })
    fireEvent.click(select)
    fireEvent.click(screen.getByRole('button', { name: 'Edit Title for track-1.mp3' }))
    const input = screen.getByRole('textbox', { name: 'Title for track-1.mp3 value' })
    fireEvent.change(input, { target: { value: 'Changed title' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(await screen.findByRole('alert')).toHaveTextContent('Metadata service unavailable')
    expect(screen.getByRole('textbox', { name: 'Title for track-1.mp3 value' })).toHaveValue('Changed title')
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.getByText('Title 1')).toBeInTheDocument()
    expect(screen.getByText('1 selected · 1 visible')).toBeInTheDocument()
  })

  it('supports inspector metadata editing without closing and exposes Save to File for unsaved changes', async () => {
    const updated = { ...allTracks[0], title: 'Inspector title', album: 'Inspector album', preparation_state: preparation(1, 'UNSAVED', ['Working metadata differs from file tags']) }
    let fetchCount = 0
    vi.mocked(workspaceApi.fetchInboxTracks).mockImplementation(async () => {
      fetchCount += 1
      return page(fetchCount === 1 ? allTracks : [updated, ...allTracks.slice(1)], 3)
    })
    vi.mocked(workspaceApi.patchInboxTrack).mockResolvedValue({
      track_id: 1,
      rename: null,
      metadata: { ...updated, track_id: 1, status: 'updated', fields_changed: ['title'], tag_write: null },
      errors: [],
    })
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect track-1.mp3' }))
    const inspector = screen.getByRole('dialog', { name: 'Inbox Track Inspector' })
    fireEvent.click(within(inspector).getByRole('tab', { name: 'Metadata' }))
    fireEvent.click(within(inspector).getByRole('button', { name: 'Edit Title' }))
    const input = within(inspector).getByRole('textbox', { name: 'Title value' })
    fireEvent.change(input, { target: { value: 'Inspector title' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(workspaceApi.patchInboxTrack).toHaveBeenCalledWith(1, { title: 'Inspector title' }))
    expect(screen.getByRole('dialog', { name: 'Inbox Track Inspector' })).toBeInTheDocument()
    expect(within(inspector).getAllByText('Inspector title').length).toBeGreaterThan(0)
    fireEvent.click(within(inspector).getByRole('button', { name: 'Edit Album' }))
    const albumInput = within(inspector).getByRole('textbox', { name: 'Album value' })
    fireEvent.change(albumInput, { target: { value: 'Inspector album 2' } })
    fireEvent.keyDown(albumInput, { key: 'Enter' })
    await waitFor(() => expect(workspaceApi.patchInboxTrack).toHaveBeenCalledWith(1, { album: 'Inspector album 2' }))
    fireEvent.click(within(inspector).getByRole('tab', { name: 'Status' }))
    expect(within(inspector).getAllByText('Unsaved').length).toBeGreaterThan(0)
    expect(within(inspector).getByText('Changes not yet written to file')).toBeInTheDocument()
    expect(within(inspector).getByRole('button', { name: 'Save to File' })).toBeInTheDocument()
  })

  it('bulk edits Genre, Comment, and Label with explicit operations, preview, and confirmation', async () => {
    vi.mocked(workspaceApi.previewInboxBulkEdit).mockResolvedValue({
      selected_count: 3,
      eligible_count: 3,
      changeable_count: 3,
      skipped_not_inbox: 0,
      missing_count: 0,
      unsupported_count: 0,
      fields: {
        genre: { operation: 'set', value: 'Afro House', current_values: ['House'], mixed: false, affected_count: 3, already_matching_count: 0, skipped_count: 0 },
        comment: { operation: 'append', value: 'Warm-up', current_values: ['Blank'], mixed: false, affected_count: 3, already_matching_count: 0, skipped_count: 0 },
        label: { operation: 'set', value: 'Soulistic', current_values: ['Blank'], mixed: false, affected_count: 3, already_matching_count: 0, skipped_count: 0 },
      },
      items: [],
      message: 'Preview only. No metadata, tags, backups, or files were changed.',
    })
    vi.mocked(workspaceApi.applyInboxBulkEdit).mockResolvedValue({
      selected_count: 3, changed_count: 3, unchanged_count: 0, succeeded_count: 3,
      failed_count: 0, skipped_count: 0, not_found_count: 0, results: [],
      tag_write: { operation_ids: ['op-1'], used_verified_writer: true },
      message: 'Bulk metadata was applied through the existing backup, write, re-read, and verify path.',
    })
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select visible page (3 tracks)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit Metadata (3)' }))
    fireEvent.change(screen.getByLabelText('Genre'), { target: { value: 'set' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Genre bulk value' }), { target: { value: 'Afro House' } })
    fireEvent.change(screen.getByLabelText('Comment'), { target: { value: 'append' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Comment bulk value' }), { target: { value: 'Warm-up' } })
    fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'set' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Label bulk value' }), { target: { value: 'Soulistic' } })
    expect(screen.queryByLabelText('Title')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Artist')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))
    await screen.findByText(/3 selected · 3 affected/)
    expect(screen.getByText('3 will append “Warm-up”')).toBeInTheDocument()
    expect(workspaceApi.applyInboxBulkEdit).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Review & apply' }))
    expect(screen.getByRole('group', { name: 'Apply metadata to 3 tracks?' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }))
    await waitFor(() => expect(workspaceApi.applyInboxBulkEdit).toHaveBeenCalledWith([1, 2, 3], {
      genre: { operation: 'set', value: 'Afro House' },
      comment: { operation: 'append', value: 'Warm-up' },
      label: { operation: 'set', value: 'Soulistic' },
    }))
    expect(screen.getByText(/3 written and verified/)).toBeInTheDocument()
  })

  it.each([
    {
      name: 'some selected tracks already match',
      preview: { selected_count: 3, eligible_count: 3, changeable_count: 1, skipped_not_inbox: 0, missing_count: 0 },
      expected: /3 selected · 1 affected · 2 unchanged across selected operations/,
    },
    {
      name: 'no selected tracks would change',
      preview: { selected_count: 3, eligible_count: 3, changeable_count: 0, skipped_not_inbox: 0, missing_count: 0 },
      expected: /3 selected · 0 affected · 3 unchanged across selected operations/,
    },
    {
      name: 'skips ineligible tracks',
      preview: { selected_count: 3, eligible_count: 2, changeable_count: 1, skipped_not_inbox: 1, missing_count: 0 },
      expected: /3 selected · 1 affected · 1 unchanged across selected operations · 1 outside Inbox/,
    },
  ])('renders truthful bulk-preview counts when $name', async ({ preview, expected }) => {
    vi.mocked(workspaceApi.previewInboxBulkEdit).mockResolvedValue({
      ...preview,
      unsupported_count: 0,
      fields: { genre: { operation: 'set', value: 'Afro House', current_values: ['House'], mixed: false, affected_count: preview.changeable_count, already_matching_count: preview.eligible_count - preview.changeable_count, skipped_count: preview.skipped_not_inbox } },
      items: [],
      message: 'Preview only. No metadata, tags, backups, or files were changed.',
    })
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select visible page (3 tracks)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit Metadata (3)' }))
    fireEvent.change(screen.getByLabelText('Genre'), { target: { value: 'set' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Genre bulk value' }), { target: { value: 'Afro House' } })
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))

    expect(await screen.findByText(expected)).toBeInTheDocument()
  })

  it('refreshes authoritative preparation state and primary Unsaved count after inline metadata edit', async () => {
    const updatedState = preparation(1, 'UNSAVED', ['Artist has changes not yet written to file'])
    const updatedTrack = { ...allTracks[0], artist: 'Updated Artist', preparation_state: updatedState }
    let fetchCount = 0
    vi.mocked(workspaceApi.fetchInboxTracks).mockImplementation(async () => {
      fetchCount += 1
      const counts = fetchCount === 1 ? { ...statusCounts, UNSAVED: 0 } : { ...statusCounts, UNSAVED: 1, READY: 0 }
      return page(fetchCount === 1 ? allTracks : [updatedTrack, allTracks[1], allTracks[2]], 3, counts)
    })
    vi.mocked(workspaceApi.patchInboxTrack).mockResolvedValue({
      track_id: 1,
      rename: null,
      metadata: {
        track_id: 1, status: 'updated', fields_changed: ['artist'], artist: 'Updated Artist', title: 'Title 1', genre: 'House', album: null,
        tag_write: null, preparation_state: updatedState,
      },
      errors: [],
    })

    render(<MemoryRouter><Inbox /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Artist for track-1.mp3' }))
    const input = screen.getByRole('textbox', { name: 'Artist for track-1.mp3 value' })
    fireEvent.change(input, { target: { value: 'Updated Artist' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => expect(workspaceApi.patchInboxTrack).toHaveBeenCalledWith(1, { artist: 'Updated Artist' }))
    await waitFor(() => expect(workspaceApi.fetchInboxTracks).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('button', { name: 'Unsaved 1' })).toBeInTheDocument()
    expect(screen.getByText('Updated Artist')).toBeInTheDocument()
    expect(updatedState.pending_fields).toEqual(['artist'])
    expect(updatedState.write.has_unsaved_changes).toBe(true)
  })

  it('keeps higher-precedence status counts while exposing pending unsaved fields', async () => {
    const blockedWithPending = preparation(1, 'NEEDS_ATTENTION', ['Genre is missing'])
    blockedWithPending.pending_fields = ['artist']
    blockedWithPending.write.has_unsaved_changes = true
    const item = { ...allTracks[0], preparation_state: blockedWithPending }
    vi.mocked(workspaceApi.fetchInboxTracks).mockResolvedValue(page([item], 1, {
      ALL: 1, WRITE_BLOCKED: 0, NEEDS_ATTENTION: 1, REVIEW: 0, UNSAVED: 0, READY: 0,
    }))
    render(<MemoryRouter><Inbox /></MemoryRouter>)

    expect(await screen.findByRole('button', { name: 'Unsaved 0' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Inspect track-1.mp3' }))
    const inspector = await screen.findByRole('dialog', { name: 'Inbox Track Inspector' })
    expect(within(inspector).getByText('Needs Attention')).toBeInTheDocument()
    expect(within(inspector).getByText('artist')).toBeInTheDocument()
    expect(within(inspector).getByText('Yes')).toBeInTheDocument()
  })

  it('keeps Title and Artist out of bulk editing while single-track Title remains available', async () => {
    render(<MemoryRouter><Inbox /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select visible page (3 tracks)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit Metadata (3)' }))
    expect(screen.queryByLabelText('Title')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Artist')).not.toBeInTheDocument()
    expect(screen.getByText('Title, Artist, and Filename stay track-specific and cannot be edited in bulk.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Preview changes' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Edit Title for track-1.mp3' })).toBeInTheDocument()
    expect(workspaceApi.previewInboxBulkEdit).not.toHaveBeenCalled()
  })
})
