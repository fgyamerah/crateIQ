import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { fetchCrates } from '../api/crates'
import {
  confirmPublishExport,
  fetchPublishOperations,
  fetchPublishReadiness,
  previewPublishExport,
} from '../api/publish'
import type { CrateSummary } from '../types/crate'
import Publish from './Publish'

vi.mock('../api/crates', () => ({ fetchCrates: vi.fn() }))
vi.mock('../api/publish', () => ({
  confirmPublishExport: vi.fn(),
  confirmPublishSync: vi.fn(),
  fetchPublishOperations: vi.fn(),
  fetchPublishReadiness: vi.fn(),
  fetchPublishSyncStatus: vi.fn(),
  previewPublishExport: vi.fn(),
  previewPublishSync: vi.fn(),
}))

const crates = [
  { id: 1, name: 'First crate', notes: null, created_at: '2026-08-11T00:00:00Z', updated_at: '2026-08-11T00:00:00Z', track_count: 3 },
  { id: 2, name: 'Second crate', notes: null, created_at: '2026-08-11T00:00:00Z', updated_at: '2026-08-11T00:00:00Z', track_count: 2 },
] satisfies CrateSummary[]

const readiness = {
  crate_id: 1,
  crate_name: 'First crate',
  track_count: 3,
  export_target: 'm3u8' as const,
  export_destination_category: 'managed exports',
  export_ready: true,
  sync_source: 'library' as const,
  sync_destination_category: 'configured SSD',
  sync_ready: true,
  blockers: [],
  warnings: [],
  conflicts: [],
  confirmation_required: true,
  next_operation: 'export' as const,
}

const exportPreview = {
  crate_id: 1,
  crate_name: 'First crate',
  export_target: 'm3u8' as const,
  target_path: 'exports/First crate.m3u8',
  target_exists: false,
  proposed_filename: 'First crate.m3u8',
  track_count: 3,
  warnings: [],
  blockers: [],
  no_overwrite: true,
  confirmation_required: true,
}

function deferred<T>() {
  let resolve: (value: T) => void
  const promise = new Promise<T>((nextResolve) => { resolve = nextResolve })
  return { promise, resolve: resolve! }
}

describe('Publish safety gates', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(fetchCrates).mockResolvedValue(crates)
    vi.mocked(fetchPublishReadiness).mockResolvedValue(readiness)
    vi.mocked(fetchPublishOperations).mockResolvedValue([])
  })

  it('does not dispatch export confirmation before a preview exists', async () => {
    render(<Publish />)

    const confirm = await screen.findByRole('button', { name: /confirm & export/i })
    expect(confirm).toBeDisabled()

    fireEvent.click(confirm)
    expect(confirmPublishExport).not.toHaveBeenCalled()
  })

  it('invalidates a confirmed export preview when the selected crate changes', async () => {
    vi.mocked(previewPublishExport).mockResolvedValue(exportPreview)
    render(<Publish />)

    const previewButton = await screen.findByRole('button', { name: /preview export/i })
    await waitFor(() => expect(previewButton).toBeEnabled())
    fireEvent.click(previewButton)
    await screen.findByText('First crate.m3u8')
    expect(screen.getByRole('button', { name: /confirm & export/i })).toBeEnabled()

    fireEvent.change(screen.getByLabelText('Crate'), { target: { value: '2' } })

    expect(screen.getByRole('button', { name: /confirm & export/i })).toBeDisabled()
    expect(screen.queryByText('First crate.m3u8')).not.toBeInTheDocument()
    expect(confirmPublishExport).not.toHaveBeenCalled()
  })

  it('ignores an in-flight export preview after the selected crate changes', async () => {
    const pendingPreview = deferred<typeof exportPreview>()
    const replacementPreview = {
      ...exportPreview,
      crate_id: 2,
      crate_name: 'Second crate',
      target_path: 'exports/Second crate.m3u8',
      proposed_filename: 'Second crate.m3u8',
      track_count: 2,
    }
    vi.mocked(previewPublishExport)
      .mockReturnValueOnce(pendingPreview.promise)
      .mockResolvedValueOnce(replacementPreview)
    render(<Publish />)

    const previewButton = await screen.findByRole('button', { name: /preview export/i })
    await waitFor(() => expect(previewButton).toBeEnabled())
    fireEvent.click(previewButton)
    await waitFor(() => expect(previewPublishExport).toHaveBeenCalledWith(1, 'm3u8'))

    fireEvent.change(screen.getByLabelText('Crate'), { target: { value: '2' } })
    await waitFor(() => expect(previewButton).toBeEnabled())
    fireEvent.click(previewButton)
    await screen.findByText('Second crate.m3u8')
    expect(previewPublishExport).toHaveBeenLastCalledWith(2, 'm3u8')

    await act(async () => { pendingPreview.resolve(exportPreview) })

    expect(screen.getByRole('button', { name: /confirm & export/i })).toBeEnabled()
    expect(screen.queryByText('First crate.m3u8')).not.toBeInTheDocument()
    expect(confirmPublishExport).not.toHaveBeenCalled()
  })
})
