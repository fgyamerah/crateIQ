import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import UnifiedWaveform from './UnifiedWaveform'

class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', MockResizeObserver)
})

describe('UnifiedWaveform', () => {
  it('renders an animated skeleton while a waveform is being prepared', () => {
    const { container } = render(<UnifiedWaveform status="processing" />)
    expect(container.querySelector('.unified-waveform-skeleton')).toBeTruthy()
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', 'Preparing waveform')
  })

  it('renders a muted center line when no waveform exists', () => {
    const { container } = render(<UnifiedWaveform status="not_generated" />)
    expect(container.querySelector('.unified-waveform-empty-line')).toBeTruthy()
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', 'No waveform generated for this track')
  })

  it('renders the real waveform canvas when ready', () => {
    const { container } = render(
      <UnifiedWaveform
        status="ready"
        peaks={[0, 100, -100, 50, -50, 200]}
        colorBands={[0.2, 0.5, 0.3, 0.8, 0.1, 0.1]}
        duration={4}
        currentTime={1}
      />,
    )
    expect(container.querySelector('canvas')).toBeTruthy()
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', 'Waveform of the current track')
  })
})
