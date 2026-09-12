import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, Heart, Loader2, Star } from 'lucide-react'
import { ApiError } from '../../api/client'
import {
  applyReviewSignals,
  previewReviewSignals,
  type ReviewSignalApplyResult,
  type ReviewSignalOperation,
  type ReviewSignalPreview,
} from '../../api/reviews'
import type { TrackSummary } from '../../types/track'
import StatusStrip from '../ui/StatusStrip'

interface Props {
  trackIds: number[]
  tracks: TrackSummary[]
  onApplied: () => Promise<void> | void
  onClose: () => void
}

type RatingAction = 'leave' | 'set' | 'clear'
type FavoriteAction = 'leave' | 'add' | 'remove'

function messageFor(error: unknown) {
  return error instanceof ApiError ? error.displayMessage : error instanceof Error ? error.message : 'Could not update rating or Favorites.'
}

function mixedValue<T>(values: T[], empty: T): T | 'Mixed' {
  if (!values.length) return empty
  return values.every((value) => value === values[0]) ? values[0] : 'Mixed'
}

export default function BulkRatingFavoriteEditor({ trackIds, tracks, onApplied, onClose }: Props) {
  const [ratingAction, setRatingAction] = useState<RatingAction>('leave')
  const [ratingValue, setRatingValue] = useState(3)
  const [favoriteAction, setFavoriteAction] = useState<FavoriteAction>('leave')
  const [preview, setPreview] = useState<ReviewSignalPreview | null>(null)
  const [result, setResult] = useState<ReviewSignalApplyResult | null>(null)
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const confirmButtonRef = useRef<HTMLButtonElement>(null)

  const operations = useMemo(() => {
    const next: Partial<Record<'rating' | 'favorite', ReviewSignalOperation>> = {}
    if (ratingAction === 'set') next.rating = { operation: 'set', value: ratingValue }
    if (ratingAction === 'clear') next.rating = { operation: 'clear' }
    if (favoriteAction === 'add') next.favorite = { operation: 'set', value: true }
    if (favoriteAction === 'remove') next.favorite = { operation: 'set', value: false }
    return next
  }, [favoriteAction, ratingAction, ratingValue])

  useEffect(() => { if (confirming) confirmButtonRef.current?.focus() }, [confirming])

  function resetPreview() {
    setPreview(null)
    setResult(null)
    setConfirming(false)
    setError(null)
  }

  async function runPreview() {
    if (!Object.keys(operations).length) return
    setBusy('preview')
    setError(null)
    try {
      setPreview(await previewReviewSignals(trackIds, operations))
      setResult(null)
    } catch (previewError) {
      setError(messageFor(previewError))
    } finally {
      setBusy(null)
    }
  }

  async function apply() {
    setBusy('apply')
    setError(null)
    try {
      setResult(await applyReviewSignals(trackIds, operations))
      setConfirming(false)
      await onApplied()
    } catch (applyError) {
      setError(messageFor(applyError))
    } finally {
      setBusy(null)
    }
  }

  const currentRating = mixedValue(tracks.map((track) => track.rating ?? null), null)
  const currentFavorite = mixedValue(tracks.map((track) => Boolean(track.favorite)), false)

  return (
    <section className="card settings-card inbox-bulk-edit" aria-labelledby="bulk-rating-title">
      <div className="inbox-bulk-panel-head">
        <div>
          <h2 className="card-title" id="bulk-rating-title"><Star size={16} /> Rating &amp; Favorites</h2>
          <p className="muted">Selected: {trackIds.length} track{trackIds.length === 1 ? '' : 's'} · DB-only signals</p>
        </div>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>Close</button>
      </div>

      <div className="inbox-bulk-signal-grid">
        <div className="inbox-bulk-edit-field">
          <div className="inbox-bulk-field-label"><label htmlFor="bulk-rating-operation"><Star size={13} /> Rating</label><span>Current: {currentRating === 'Mixed' ? 'Mixed' : currentRating === null ? 'Unrated' : `${currentRating}/5`}</span></div>
          <select id="bulk-rating-operation" className="form-input" value={ratingAction} disabled={busy !== null} onChange={(event) => { setRatingAction(event.target.value as RatingAction); resetPreview() }}>
            <option value="leave">Leave unchanged</option>
            <option value="set">Set rating</option>
            <option value="clear">Clear rating</option>
          </select>
          {ratingAction === 'set' && (
            <select id="bulk-rating-value" className="form-input" value={ratingValue} disabled={busy !== null} aria-label="Rating to set" onChange={(event) => { setRatingValue(Number(event.target.value)); resetPreview() }}>
              {[1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{'★'.repeat(value)}{'☆'.repeat(5 - value)} · {value} star{value === 1 ? '' : 's'}</option>)}
            </select>
          )}
        </div>
        <div className="inbox-bulk-edit-field">
          <div className="inbox-bulk-field-label"><label htmlFor="bulk-favorite-operation"><Heart size={13} /> Favorites</label><span>Current: {currentFavorite === 'Mixed' ? 'Mixed' : currentFavorite ? 'All favorites' : 'None'}</span></div>
          <select id="bulk-favorite-operation" className="form-input" value={favoriteAction} disabled={busy !== null} onChange={(event) => { setFavoriteAction(event.target.value as FavoriteAction); resetPreview() }}>
            <option value="leave">Leave unchanged</option>
            <option value="add">Add all to Favorites</option>
            <option value="remove">Remove all from Favorites</option>
          </select>
        </div>
      </div>

      {error && <StatusStrip tone="danger" onDismiss={() => setError(null)}>{error}</StatusStrip>}

      <div className="settings-actions inbox-bulk-primary-actions">
        <button type="button" className="btn btn--ghost btn--sm" disabled={!Object.keys(operations).length || busy !== null || confirming} onClick={() => void runPreview()}>
          {busy === 'preview' ? <Loader2 size={13} className="spin" /> : null} Preview changes
        </button>
      </div>

      {preview && (
        <div className="inbox-bulk-edit-preview" aria-label="Rating and Favorites preview">
          <h3>Preview changes</h3>
          <p className="inbox-bulk-edit-impact">{preview.selected_count} selected · {preview.changeable_count} will change · {preview.selected_count - preview.changeable_count} already match{preview.missing_count ? ` · ${preview.missing_count} not found` : ''}</p>
          <div className="inbox-bulk-preview-grid">
            {Object.entries(preview.fields).map(([field, item]) => item && (
              <div className="inbox-bulk-edit-preview-field" key={field}>
                <strong>{field === 'rating' ? 'Rating' : 'Favorites'}</strong>
                <span>{field === 'rating' ? (item.operation === 'clear' ? 'Clear rating' : `Set to ${item.value} stars`) : (item.value ? 'Add to Favorites' : 'Remove from Favorites')}</span>
                <small>{item.affected_count} will change · {item.already_matching_count} already match{item.mixed ? ' · current state Mixed' : ''}</small>
              </div>
            ))}
          </div>
          <div className="settings-actions">
            <button type="button" className="btn btn--primary btn--sm" disabled={!preview.changeable_count || busy !== null} onClick={() => setConfirming(true)}>Review &amp; apply</button>
          </div>
          {confirming && (
            <div className="inbox-bulk-confirm" role="group" aria-label="Confirm rating and Favorites changes">
              <p>Apply these review signals to {preview.changeable_count} track{preview.changeable_count === 1 ? '' : 's'}? This will not write tags or change audio files.</p>
              <div className="settings-actions">
                <button ref={confirmButtonRef} type="button" className="btn btn--primary btn--sm" disabled={busy !== null} onClick={() => void apply()}>{busy === 'apply' ? <Loader2 size={13} className="spin" /> : <Check size={13} />} Apply</button>
                <button type="button" className="btn btn--ghost btn--sm" disabled={busy !== null} onClick={() => setConfirming(false)}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}

      {result && <StatusStrip tone="good">{result.succeeded_count} changed · {result.unchanged_count} already matched. Favorites and ratings are saved in the library review database.</StatusStrip>}
    </section>
  )
}
