import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('./components/Layout', async () => {
  const React = await import('react')
  const { Outlet } = await import('react-router-dom')
  return { default: () => React.createElement(Outlet) }
})
vi.mock('./components/player/PersistentPlayerProvider', () => ({
  default: ({ children }: { children: unknown }) => children,
}))
vi.mock('./components/library/LibraryView', () => ({ default: () => 'Library destination' }))
vi.mock('./pages/Inbox', () => ({ default: () => 'Inbox destination' }))
vi.mock('./pages/Listening', async () => {
  const { useLocation } = await import('react-router-dom')
  return {
    default: () => {
      const location = useLocation()
      return `Music review destination: ${location.pathname}${location.search}${location.hash}`
    },
  }
})

import App from './App'

describe('legacy route redirects', () => {
  it.each([
    ['/dashboard', 'Library destination'],
    ['/collection', 'Library destination'],
    ['/tracks', 'Library destination'],
    ['/library-prep', 'Inbox destination'],
    ['/listening?tab=queue#now', 'Music review destination: /music-review?tab=queue#now'],
  ])('redirects %s to its supported destination', async (source, destination) => {
    window.history.pushState({}, '', source)
    render(<App />)

    expect(await screen.findByText(destination)).toBeVisible()
  })
})
