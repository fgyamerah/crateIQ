import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { fetchReadiness } from '../api/runtime'
import ReadinessBanner from './ReadinessBanner'

vi.mock('../api/runtime', () => ({ fetchReadiness: vi.fn() }))

describe('ReadinessBanner', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('renders a truthful diagnostic when the readiness read fails', async () => {
    vi.mocked(fetchReadiness).mockRejectedValue(new Error('offline'))
    render(<ReadinessBanner />)

    expect(await screen.findByText('Runtime readiness could not be checked.')).toBeVisible()
  })

  it('surfaces a degraded runtime state and its safe diagnostic', async () => {
    vi.mocked(fetchReadiness).mockResolvedValue({
      status: 'degraded',
      checks: [{ name: 'workspace', status: 'warn', message: 'Workspace needs attention', required: true }],
    })
    render(<ReadinessBanner />)

    expect(await screen.findByText('Runtime degraded')).toBeVisible()
    expect(screen.getByText('Workspace needs attention')).toBeVisible()
  })
})
