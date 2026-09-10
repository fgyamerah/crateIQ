import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import * as tagWriteApi from '../../api/tagWrite'
import type { TagWritePlan, TagWritePlanItem } from '../../types/tagWrite'
import SaveToFileDialog from './SaveToFileDialog'
import { chunkTrackIds } from './saveToFilePlan'

vi.mock('../../api/tagWrite', () => ({
  applyTagWritePlan: vi.fn(),
  fetchTagWritePlan: vi.fn(),
}))

function item(trackId: number, kind: 'change' | 'noop' | 'blocked' = 'change'): TagWritePlanItem {
  return {
    track_id: trackId,
    filename: `track-${trackId}.${kind === 'blocked' ? 'm4a' : 'mp3'}`,
    relative_path: `Inbox/track-${trackId}.mp3`,
    blocked: kind === 'blocked',
    blocker: kind === 'blocked' ? 'M4A is not a supported write-back format.' : null,
    fields: kind === 'change' ? [{ field: 'artist', current_file_value: 'Old Artist', approved_value: 'New Artist', action: 'REPLACE' }] : [],
    ...(kind === 'blocked' ? {} : { expected_size: 100, expected_mtime_ns: '123' }),
  }
}

function plan(items: TagWritePlanItem[]): TagWritePlan {
  return {
    items,
    track_count: items.length,
    changeable_count: items.filter((entry) => !entry.blocked && entry.fields.length).length,
    no_op_count: items.filter((entry) => !entry.blocked && !entry.fields.length).length,
    blocked_count: items.filter((entry) => entry.blocked).length,
    additions: 0,
    replacements: items.filter((entry) => entry.fields.length).length,
    clears: 0,
    backup_space_estimate_bytes: 100,
    writable_fields: ['artist', 'title', 'album', 'genre'],
    supported_formats: ['.flac', '.mp3'],
    message: 'Preview only. No file, backup, or local index changes were made.',
  }
}

describe('SaveToFileDialog', () => {
  beforeEach(() => { vi.resetAllMocks() })

  it('keeps the writer boundary at 50 tracks for large selections', () => {
    expect(chunkTrackIds([1])).toEqual([[1]])
    expect(chunkTrackIds(Array.from({ length: 50 }, (_, index) => index + 1))).toHaveLength(1)
    expect(chunkTrackIds(Array.from({ length: 200 }, (_, index) => index + 1)).map((chunk) => chunk.length)).toEqual([50, 50, 50, 50])
  })

  it('previews selected tracks, calls the existing writer, and chunks at 50', async () => {
    vi.mocked(tagWriteApi.fetchTagWritePlan).mockImplementation(async (ids) => plan(ids.map((id) => item(id))))
    vi.mocked(tagWriteApi.applyTagWritePlan).mockImplementation(async (items) => ({
      operation_id: `operation-${items[0].track_id}`,
      status: 'completed',
      applied: items.length,
      skipped: 0,
      failed: 0,
      results: items.map((entry) => ({ track_id: entry.track_id, status: 'applied', fields: ['artist'], verified: true })),
    }))
    const onApplied = vi.fn()
    render(<SaveToFileDialog trackIds={Array.from({ length: 51 }, (_, index) => index + 1)} onClose={vi.fn()} onApplied={onApplied} />)

    expect(await screen.findByRole('heading', { name: 'Save to File — 51 selected' })).toBeInTheDocument()
    expect(tagWriteApi.fetchTagWritePlan).toHaveBeenCalledTimes(2)
    expect(vi.mocked(tagWriteApi.fetchTagWritePlan).mock.calls.map(([ids]) => ids.length)).toEqual([50, 1])
    expect(screen.getByText('51 tracks have changes')).toBeInTheDocument()
    expect(tagWriteApi.applyTagWritePlan).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Review & Save' }))
    expect(screen.getByRole('alertdialog', { name: 'Confirm Save to File' })).toBeInTheDocument()
    expect(screen.getByText(/Only managed Inbox copies will be modified/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save to File' }))

    await waitFor(() => expect(tagWriteApi.applyTagWritePlan).toHaveBeenCalledTimes(2))
    expect(vi.mocked(tagWriteApi.applyTagWritePlan).mock.calls.map(([items]) => items.length)).toEqual([50, 1])
    expect(await screen.findByText('Saved to file: 51')).toBeInTheDocument()
    expect(onApplied).toHaveBeenCalledTimes(1)
  })

  it('shows no-op and blocked tracks in the preview and does not apply them', async () => {
    vi.mocked(tagWriteApi.fetchTagWritePlan).mockResolvedValue(plan([item(1), item(2, 'noop'), item(3, 'blocked')]))
    render(<SaveToFileDialog trackIds={[1, 2, 3]} onClose={vi.fn()} onApplied={vi.fn()} />)

    expect(await screen.findByText('1 track has changes')).toBeInTheDocument()
    expect(screen.getByText(/already matches/)).toBeInTheDocument()
    expect(screen.getByText(/cannot be written/)).toBeInTheDocument()
    expect(screen.getByText(/Write blocked: M4A/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Review & Save' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save to File' }))
    await waitFor(() => expect(tagWriteApi.applyTagWritePlan).toHaveBeenCalledTimes(1))
    expect(tagWriteApi.applyTagWritePlan).toHaveBeenCalledWith([expect.objectContaining({ track_id: 1 })])
  })

  it('does not claim saved when the writer result is not verified', async () => {
    vi.mocked(tagWriteApi.fetchTagWritePlan).mockResolvedValue(plan([item(1)]))
    vi.mocked(tagWriteApi.applyTagWritePlan).mockResolvedValue({
      operation_id: 'operation-1', status: 'completed', applied: 1, skipped: 0, failed: 0,
      results: [{ track_id: 1, status: 'applied', fields: ['artist'], verified: false }],
    })
    render(<SaveToFileDialog trackIds={[1]} onClose={vi.fn()} onApplied={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Review & Save' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save to File' }))
    const result = await screen.findByRole('heading', { name: /Save to File result/ })
    expect(within(result.parentElement!).getByText('Write failed: 1')).toBeInTheDocument()
    expect(within(result.parentElement!).queryByText('Saved to file: 1')).not.toBeInTheDocument()
  })

  it('reports a stale result without automatically requesting a fresh plan', async () => {
    vi.mocked(tagWriteApi.fetchTagWritePlan).mockResolvedValue(plan([item(1)]))
    vi.mocked(tagWriteApi.applyTagWritePlan).mockResolvedValue({
      operation_id: 'operation-1', status: 'failed', applied: 0, skipped: 0, failed: 1,
      results: [{ track_id: 1, status: 'failed', reason: 'File changed since preview -- stale plan blocked. Re-run preview and try again.' }],
    })
    render(<SaveToFileDialog trackIds={[1]} onClose={vi.fn()} onApplied={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Review & Save' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save to File' }))
    expect(await screen.findByText(/stale plan blocked/)).toBeInTheDocument()
    expect(tagWriteApi.fetchTagWritePlan).toHaveBeenCalledTimes(1)
  })
})
