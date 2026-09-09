import { StrictMode } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as launcherApi from '../api/libraryLauncher'
import LibraryLauncher from './LibraryLauncher'

vi.mock('../api/libraryLauncher', () => ({
  activateRegisteredLibrary: vi.fn(),
  createLibrary: vi.fn(),
  fetchActivationStatus: vi.fn(),
  fetchBrowseDirectories: vi.fn(),
  fetchCurrentLibrary: vi.fn(),
  fetchLibraryRegistry: vi.fn(),
  registerExistingLibrary: vi.fn(),
}))

const browseResponse: launcherApi.BrowseResponse = {
  current_path: '/safe',
  parent_path: null,
  roots: [{ display_name: 'Safe', path: '/safe' }],
  entries: [{
    display_name: 'Music',
    path: '/safe/Music',
    entry_type: 'directory',
    selectable: false,
    classification: 'empty_folder',
    reason: 'Safe directory',
  }],
  offset: 0,
  limit: 50,
  truncated: false,
}

function renderLauncher() {
  return render(
    <StrictMode>
      <MemoryRouter>
        <LibraryLauncher />
      </MemoryRouter>
    </StrictMode>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(launcherApi.fetchLibraryRegistry).mockResolvedValue({
    recent_libraries: [],
    registry_status: 'ready',
    message: null,
  })
  vi.mocked(launcherApi.fetchCurrentLibrary).mockResolvedValue({
    rootless: true,
    library_root: null,
    library_id: null,
    display_name: null,
    launcher_status: 'ready',
    activation_status: 'idle',
  })
  vi.mocked(launcherApi.fetchBrowseDirectories).mockResolvedValue(browseResponse)

  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: vi.fn(function showModal(this: HTMLDialogElement) {
      this.setAttribute('open', '')
    }),
  })
})

describe('LibraryLauncher dialogs', () => {
  it.each([
    ['Browse Libraries', 'Browse Libraries'],
    ['Create New Library', 'Create New Library'],
  ])('keeps the %s dialog open and makes one initial dialog browse request', async (buttonName, title) => {
    renderLauncher()

    const button = await screen.findByRole('button', { name: buttonName })
    await waitFor(() => expect(button).toBeEnabled())
    expect(launcherApi.fetchBrowseDirectories).toHaveBeenCalledTimes(1)

    fireEvent.click(button)

    const dialog = await screen.findByRole('dialog', { name: title })
    expect(await screen.findByText('Music')).toBeVisible()
    await waitFor(() => expect(launcherApi.fetchBrowseDirectories).toHaveBeenCalledTimes(2))
    expect(dialog).toHaveAttribute('open')
    expect(vi.mocked(launcherApi.fetchBrowseDirectories).mock.calls[1]?.[1]?.aborted).toBe(false)

    if (title === 'Browse Libraries') {
      fireEvent.click(screen.getByRole('button', { name: 'Open Music' }))
      await waitFor(() => expect(launcherApi.fetchBrowseDirectories).toHaveBeenCalledTimes(3))
      expect(vi.mocked(launcherApi.fetchBrowseDirectories).mock.calls[2]?.[0]).toMatchObject({
        path: '/safe/Music',
        limit: 50,
      })
      expect(dialog).toHaveAttribute('open')
    } else {
      const nameInput = screen.getByRole('textbox', { name: 'Library name' })
      fireEvent.change(nameInput, { target: { value: 'Temporary name' } })
      expect(nameInput).toHaveValue('Temporary name')
    }

    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }))
    await waitFor(() => expect(dialog).not.toBeInTheDocument())
  })
})
