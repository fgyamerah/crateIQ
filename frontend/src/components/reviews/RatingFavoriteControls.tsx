import { useState } from 'react'
import { Heart, Star } from 'lucide-react'

interface Props {
  rating: number | null | undefined
  favorite: boolean | undefined
  onRatingChange: (rating: number | null) => Promise<void> | void
  onFavoriteChange: (favorite: boolean) => Promise<void> | void
  compact?: boolean
  disabled?: boolean
  showRating?: boolean
  showFavorite?: boolean
}

export default function RatingFavoriteControls({
  rating,
  favorite,
  onRatingChange,
  onFavoriteChange,
  compact = false,
  disabled = false,
  showRating = true,
  showFavorite = true,
}: Props) {
  const [busy, setBusy] = useState<'rating' | 'favorite' | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)
  const currentRating = rating ?? null

  async function save(kind: 'rating' | 'favorite', action: () => Promise<void> | void, message: string) {
    if (disabled || busy) return
    setBusy(kind)
    setFeedback(null)
    try {
      await action()
      setFeedback(message)
    } catch {
      setFeedback('Could not save')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className={`rating-favorite-controls${compact ? ' rating-favorite-controls--compact' : ''}`}>
      {showRating ? (
        <div className="rating-stars" role="group" aria-label="Track rating">
          {[1, 2, 3, 4, 5].map((value) => {
            const selected = currentRating !== null && value <= currentRating
            const exact = currentRating === value
            return (
              <button
                key={value}
                type="button"
                className={`rating-star${selected ? ' is-selected' : ''}`}
                disabled={disabled || busy !== null}
                aria-label={exact ? 'Clear rating' : `Rate ${value} star${value === 1 ? '' : 's'}`}
                aria-pressed={exact}
                title={exact ? 'Clear rating' : `Rate ${value} star${value === 1 ? '' : 's'}`}
                onClick={(event) => {
                  event.stopPropagation()
                  void save('rating', () => onRatingChange(exact ? null : value), exact ? 'Rating cleared' : `Rated ${value} stars`)
                }}
              >
                <Star size={compact ? 13 : 16} fill={selected ? 'currentColor' : 'none'} aria-hidden="true" />
              </button>
            )
          })}
        </div>
      ) : null}
      {showFavorite ? (
        <button
          type="button"
          className={`favorite-toggle${favorite ? ' is-favorite' : ''}`}
          disabled={disabled || busy !== null}
          aria-label={favorite ? 'Remove from Favorites' : 'Add to Favorites'}
          aria-pressed={Boolean(favorite)}
          title={favorite ? 'Remove from Favorites' : 'Add to Favorites'}
          onClick={(event) => {
            event.stopPropagation()
            void save('favorite', () => onFavoriteChange(!favorite), favorite ? 'Removed from Favorites' : 'Added to Favorites')
          }}
        >
          <Heart size={compact ? 15 : 18} fill={favorite ? 'currentColor' : 'none'} aria-hidden="true" />
        </button>
      ) : null}
      <span className="rating-favorite-feedback" aria-live="polite">{feedback}</span>
    </div>
  )
}
