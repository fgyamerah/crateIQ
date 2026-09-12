import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as workspaceApi from '../../api/workspace'
import type { InboxBulkEditPreview } from '../../api/workspace'
import type { TrackSummary } from '../../types/track'
import BulkMetadataEditor from './BulkMetadataEditor'

vi.mock('../../api/workspace', () => ({ applyInboxBulkEdit: vi.fn(), previewInboxBulkEdit: vi.fn() }))

const track = (id: number, genre: string, label: string | null): TrackSummary => ({
  id, filepath: `/managed/Inbox/${id}.mp3`, filename: `${id}.mp3`, artist: 'Artist', title: `Title ${id}`,
  genre, comment: id === 1 ? 'Intro' : 'Closing', label, bpm: 122, key_camelot: '8A', key_musical: null,
  duration_sec: 180, bitrate_kbps: 320, status: 'pending', quality_tier: 'HIGH', parse_confidence: 'HIGH',
  storage_zone: 'INBOX', issues: [],
})

describe('BulkMetadataEditor', () => {
  beforeEach(() => { vi.resetAllMocks() })

  it('shows only Genre/Comment/Label, ignores identity-field fallbacks, and sends explicit semantics', async () => {
    vi.mocked(workspaceApi.previewInboxBulkEdit).mockResolvedValue({
      selected_count: 2, eligible_count: 2, changeable_count: 2, skipped_not_inbox: 0, missing_count: 0, unsupported_count: 0,
      fields: {
        title: { operation: 'set', value: 'Unsafe shared title', current_values: ['Title 1', 'Title 2'], mixed: true, affected_count: 2, already_matching_count: 0, skipped_count: 0 },
        genre: { operation: 'set', value: 'Afro House', current_values: ['House', 'Techno'], mixed: true, affected_count: 2, already_matching_count: 0, skipped_count: 0 },
        comment: { operation: 'append', value: 'Warm-up', current_values: ['Intro', 'Closing'], mixed: true, affected_count: 2, already_matching_count: 0, skipped_count: 0 },
        label: { operation: 'clear', value: null, current_values: ['Soulistic', 'Blank'], mixed: true, affected_count: 1, already_matching_count: 1, skipped_count: 0 },
      } as unknown as InboxBulkEditPreview['fields'], items: [], message: 'Preview only',
    })
    render(<BulkMetadataEditor trackIds={[1, 2]} tracks={[track(1, 'House', 'Soulistic'), track(2, 'Techno', null)]} onApplied={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getAllByText('Current: Mixed')).toHaveLength(3)
    expect(screen.queryByRole('textbox', { name: /Title/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /Artist/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /Filename/i })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Genre')).toBeInTheDocument()
    expect(screen.getByLabelText('Comment')).toBeInTheDocument()
    expect(screen.getByLabelText('Label')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Genre bulk value' })).toHaveAttribute('id', 'bulk-genre-value')
    expect(screen.getByRole('textbox', { name: 'Genre bulk value' })).toHaveAttribute('name', 'bulk-genre-value')
    fireEvent.change(screen.getByLabelText('Genre'), { target: { value: 'set' } })
    expect(screen.getByText('Enter a value to use set.')).toBeInTheDocument()
    expect(screen.getByLabelText('Genre')).toHaveAttribute('aria-describedby', 'bulk-genre-current')
    fireEvent.change(screen.getByRole('textbox', { name: 'Genre bulk value' }), { target: { value: 'Afro House' } })
    fireEvent.change(screen.getByLabelText('Comment'), { target: { value: 'append' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Comment bulk value' }), { target: { value: 'Warm-up' } })
    fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'clear' } })
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))
    await waitFor(() => expect(workspaceApi.previewInboxBulkEdit).toHaveBeenCalledWith([1, 2], {
      genre: { operation: 'set', value: 'Afro House' },
      comment: { operation: 'append', value: 'Warm-up' },
      label: { operation: 'clear', value: undefined },
    }))
    expect(screen.queryByText('Unsafe shared title')).not.toBeInTheDocument()
    expect(screen.getByText('1 will clear')).toBeInTheDocument()
  })

  it('requires preview and confirmation, then exposes the failing track result', async () => {
    vi.mocked(workspaceApi.previewInboxBulkEdit).mockResolvedValue({
      selected_count: 2, eligible_count: 2, changeable_count: 2, skipped_not_inbox: 0, missing_count: 0, unsupported_count: 0,
      fields: {
        genre: { operation: 'set', value: 'Afro House', current_values: ['House', 'Techno'], mixed: true, affected_count: 2, already_matching_count: 0, skipped_count: 0 },
      }, items: [], message: 'Preview only',
    })
    vi.mocked(workspaceApi.applyInboxBulkEdit).mockResolvedValue({
      selected_count: 2, changed_count: 2, unchanged_count: 0, succeeded_count: 1, failed_count: 1,
      skipped_count: 0, not_found_count: 0,
      results: [
        { track_id: 1, status: 'succeeded', write_status: 'applied' },
        { track_id: 2, status: 'failed', write_status: 'failed', reason: 'Verification mismatch; backup preserved.' },
      ],
      tag_write: { operation_ids: ['op-1'], used_verified_writer: true }, message: 'Completed with one failure.',
    })
    render(<BulkMetadataEditor trackIds={[1, 2]} tracks={[track(1, 'House', null), track(2, 'Techno', null)]} onApplied={vi.fn()} onClose={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Genre'), { target: { value: 'set' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Genre bulk value' }), { target: { value: 'Afro House' } })
    expect(workspaceApi.applyInboxBulkEdit).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))
    const review = await screen.findByRole('button', { name: 'Review & apply' })
    fireEvent.click(review)
    expect(workspaceApi.applyInboxBulkEdit).not.toHaveBeenCalled()
    let apply = screen.getByRole('button', { name: 'Apply changes' })
    await waitFor(() => expect(apply).toHaveFocus())
    expect(screen.getByRole('button', { name: 'Preview changes' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(review).toHaveFocus())
    fireEvent.click(review)
    apply = screen.getByRole('button', { name: 'Apply changes' })
    fireEvent.click(apply)
    await waitFor(() => expect(workspaceApi.applyInboxBulkEdit).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByLabelText('Bulk metadata results')).toHaveFocus())
    const attention = screen.getByRole('list', { name: 'Tracks requiring attention' })
    expect(attention).toHaveTextContent('Track 2')
    expect(attention).toHaveTextContent('Verification mismatch; backup preserved.')
  })

  it('identifies tracks and reasons that will be skipped before confirmation', async () => {
    vi.mocked(workspaceApi.previewInboxBulkEdit).mockResolvedValue({
      selected_count: 2, eligible_count: 1, changeable_count: 1, skipped_not_inbox: 0, missing_count: 0, unsupported_count: 1,
      fields: {
        genre: { operation: 'set', value: 'Afro House', current_values: ['House'], mixed: false, affected_count: 1, already_matching_count: 0, skipped_count: 1 },
      },
      items: [
        { track_id: 1, filename: '1.mp3', status: 'change', reason: null },
        { track_id: 2, filename: '2.wav', status: 'unsupported', reason: 'WAV tag write-back is not supported.' },
      ],
      message: 'Preview only',
    })
    render(<BulkMetadataEditor trackIds={[1, 2]} tracks={[track(1, 'House', null), track(2, 'Techno', null)]} onApplied={vi.fn()} onClose={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Genre'), { target: { value: 'set' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Genre bulk value' }), { target: { value: 'Afro House' } })
    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))

    const skipped = await screen.findByRole('list', { name: 'Tracks that will be skipped' })
    expect(skipped).toHaveTextContent('2.wav')
    expect(skipped).toHaveTextContent('WAV tag write-back is not supported.')
  })
})
