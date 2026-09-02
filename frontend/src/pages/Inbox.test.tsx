import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
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
    vi.mocked(workspaceApi.fetchInboxTracks).mockResolvedValue({
      items: states.map((state) => track(state.track_id, state)), limit: 200, offset: 0, total: 5,
    })
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
