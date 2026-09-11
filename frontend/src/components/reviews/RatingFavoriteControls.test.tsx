import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import RatingFavoriteControls from './RatingFavoriteControls'

describe('RatingFavoriteControls', () => {
  it('exposes unrated star labels, independent favorite state, and direct keyboard-safe actions', async () => {
    const onRatingChange = vi.fn()
    const onFavoriteChange = vi.fn()
    const rowClick = vi.fn()
    render(
      <div onClick={rowClick}>
        <RatingFavoriteControls rating={null} favorite={false} onRatingChange={onRatingChange} onFavoriteChange={onFavoriteChange} />
      </div>,
    )
    expect(screen.getByRole('group', { name: 'Track rating' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rate 3 stars' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add to Favorites' })).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(screen.getByRole('button', { name: 'Rate 3 stars' }))
    await waitFor(() => expect(onRatingChange).toHaveBeenCalledWith(3))
    fireEvent.click(screen.getByRole('button', { name: 'Add to Favorites' }))
    await waitFor(() => {
      expect(onFavoriteChange).toHaveBeenCalledWith(true)
    })
    expect(rowClick).not.toHaveBeenCalled()
  })

  it('clears an existing rating when the same star is clicked', async () => {
    const onRatingChange = vi.fn()
    render(<RatingFavoriteControls rating={4} favorite={true} onRatingChange={onRatingChange} onFavoriteChange={vi.fn()} />)
    const clear = screen.getByRole('button', { name: 'Clear rating' })
    fireEvent.click(clear)
    await waitFor(() => expect(onRatingChange).toHaveBeenCalledWith(null))
    expect(screen.getByRole('button', { name: 'Remove from Favorites' })).toHaveAttribute('aria-pressed', 'true')
  })
})
