import { useEffect, useState } from 'react'
import { CheckCircle2, AlertTriangle, Pause, Play } from 'lucide-react'
import type { TrackSummary } from '../../types/track'
import type { ReviewSummary } from '../../api/reviews'
import ReviewStatusBadge from '../reviews/ReviewStatusBadge'
import type { Density, QualityTierValue, SortKey, SortOrder } from './libraryUtils'
import {
  ROW_HEIGHT,
  TABLE_VIEWPORT_HEIGHT,
  TRACK_OVERSCAN,
  camelotStyle,
  camelotTextColor,
  displayValue,
  qualityRank,
} from './libraryUtils'

interface Props {
  items: TrackSummary[]
  total: number
  loading: boolean
  offset: number
  selectedId: number | null
  sort: SortKey
  order: SortOrder
  density: Density
  reviews: ReviewSummary
  playingTrackId: number | null
  onSort: (key: SortKey) => void
  onSelect: (id: number) => void
  onPlay: (id: number) => void
  onPrevPage: () => void
  onNextPage: () => void
  onOpenImportWizard: () => void
}

// Below this width the fixed 10-column desktop table cannot show readable
// content (verified: even the 760px "intermediate" width already truncates
// every column to a few characters). Switches to a stacked card list instead
// of compressing columns further. Keep in sync with the matching
// `@media (max-width: 860px)` rule in index.css for `.lib-track-cards`.
const CARD_VIEW_QUERY = '(max-width: 860px)'

const QUALITY_COLORS: Record<number, string> = {
  4: 'var(--brand-violet)',
  3: '#34d399',
  2: '#fbbf24',
  1: '#ef4444',
  0: 'var(--text-muted)',
}

function QualityMeter({ tier }: { tier: QualityTierValue }) {
  const rank = qualityRank(tier)
  const color = QUALITY_COLORS[rank]
  return (
    <div className="lib-quality-meter" title={tier ?? 'UNKNOWN'}>
      <div className="lib-quality-meter-bars" aria-hidden="true">
        {[1, 2, 3, 4].map((step) => (
          <span
            key={step}
            className="lib-quality-meter-bar"
            style={{
              height: `${40 + step * 12}%`,
              background: step <= rank ? color : 'var(--border)',
            }}
          />
        ))}
      </div>
      <span className="lib-quality-meter-label" style={{ color }}>{tier ?? '—'}</span>
    </div>
  )
}

export default function TrackTable({
  items,
  total,
  loading,
  offset,
  selectedId,
  sort,
  order,
  density,
  reviews,
  playingTrackId,
  onSort,
  onSelect,
  onPlay,
  onPrevPage,
  onNextPage,
  onOpenImportWizard,
}: Props) {
  const [scrollTop, setScrollTop] = useState(0)
  const [isCardView, setIsCardView] = useState(() => window.matchMedia(CARD_VIEW_QUERY).matches)

  useEffect(() => {
    const mql = window.matchMedia(CARD_VIEW_QUERY)
    const onChange = () => setIsCardView(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  const rowHeight = ROW_HEIGHT[density]
  const virtualStart = Math.max(0, Math.floor(scrollTop / rowHeight) - TRACK_OVERSCAN)
  const visibleRowCount = Math.ceil(TABLE_VIEWPORT_HEIGHT / rowHeight) + TRACK_OVERSCAN * 2
  const virtualEnd = Math.min(items.length, virtualStart + visibleRowCount)
  const virtualRows = items.slice(virtualStart, virtualEnd)
  const virtualTopPad = virtualStart * rowHeight
  const virtualBottomPad = Math.max(0, (items.length - virtualEnd) * rowHeight)

  return (
    <section className="lib-card lib-table-card">
      <div className="lib-card-head">
        <h2>Tracks <span className="lib-card-head-count">({total.toLocaleString()})</span></h2>
        <span className="lib-muted">{loading ? 'Loading…' : `${total.toLocaleString()} matching tracks`}</span>
      </div>
      {isCardView ? (
        <div className="lib-track-cards" role="list">
          {loading && items.length === 0 && (
            Array.from({ length: 6 }).map((_, idx) => (
              <div key={`lib-card-skeleton-${idx}`} className="lib-track-card lib-track-card-skeleton" aria-hidden="true" />
            ))
          )}
          {items.map((track) => (
            <div
              key={track.id}
              className={selectedId === track.id ? 'lib-track-card lib-track-card--selected' : 'lib-track-card'}
              role="listitem"
              onClick={() => onSelect(track.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  onSelect(track.id)
                }
              }}
              aria-selected={selectedId === track.id}
              tabIndex={0}
            >
              <button
                type="button"
                className={`lib-track-card-play${playingTrackId === track.id ? ' is-playing' : ''}`}
                onClick={(event) => {
                  event.stopPropagation()
                  onSelect(track.id)
                  onPlay(track.id)
                }}
                aria-label={playingTrackId === track.id ? `Pause ${displayValue(track.title, track.filename)}` : `Play ${displayValue(track.title, track.filename)} in persistent player`}
                title={playingTrackId === track.id ? 'Pause persistent player' : 'Play in persistent player'}
              >
                {playingTrackId === track.id ? <Pause size={13} fill="currentColor" /> : <Play size={13} fill="currentColor" />}
              </button>
              <div className="lib-track-card-main">
                <strong>{displayValue(track.title, track.filename)}</strong>
                <span className="lib-track-card-sub">
                  {displayValue(track.artist)}{track.genre ? ` · ${track.genre}` : ''}
                </span>
                <div className="lib-track-card-meta">
                  <span className="lib-mono">{displayValue(track.bpm)} BPM</span>
                  {track.key_camelot
                    ? <span className="lib-camelot-chip lib-camelot-chip--sm" style={camelotStyle(track.key_camelot)}>{track.key_camelot}</span>
                    : null}
                  <QualityMeter tier={track.quality_tier} />
                  <ReviewStatusBadge
                    trackId={track.id}
                    status={reviews[String(track.id)]?.review_status}
                    rating={reviews[String(track.id)]?.rating}
                  />
                  <span className="lib-status-cell">
                    {track.issues.length === 0
                      ? <CheckCircle2 size={13} className="lib-status-ok" />
                      : <AlertTriangle size={13} className="lib-status-warn" />}
                    <span className="lib-muted">{track.issues.length === 0 ? 'Clean' : `${track.issues.length} issue${track.issues.length > 1 ? 's' : ''}`}</span>
                  </span>
                </div>
              </div>
            </div>
          ))}
          {!loading && items.length === 0 && (
            <div className="lib-empty">
              {total === 0 ? <div className="lib-empty-import"><strong>No tracks imported yet</strong><span>Set up the library, run a read-only scan preview, then import tracks into CrateIQ’s local index.</span><button className="lib-btn lib-btn--primary lib-btn--sm" type="button" onClick={onOpenImportWizard}>Open Library Setup &amp; Import</button></div> : 'No tracks match the current filters.'}
            </div>
          )}
        </div>
      ) : (
      <div
        className={`lib-table-scroll lib-table-scroll--${density}`}
        onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      >
        <table className="lib-table">
          <thead>
            <tr>
              <th className="lib-col-num">#</th>
              <th className="lib-th-sortable" onClick={() => onSort('title')}>
                Title {sort === 'title' && (order === 'asc' ? '▲' : '▼')}
              </th>
              <th className="lib-th-sortable" onClick={() => onSort('artist')}>
                Artist {sort === 'artist' && (order === 'asc' ? '▲' : '▼')}
              </th>
              <th className="lib-th-sortable" onClick={() => onSort('bpm')}>
                BPM {sort === 'bpm' && (order === 'asc' ? '▲' : '▼')}
              </th>
              <th>Key</th>
              <th>Camelot</th>
              <th>Genre</th>
              <th>Quality</th>
              <th>Review</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {loading && items.length === 0 && (
              Array.from({ length: 8 }).map((_, idx) => (
                <tr key={`lib-skeleton-${idx}`} className="lib-row-skeleton">
                  <td colSpan={10}><span /></td>
                </tr>
              ))
            )}
            {virtualTopPad > 0 && (
              <tr aria-hidden="true"><td colSpan={10} style={{ height: virtualTopPad, padding: 0, border: 0 }} /></tr>
            )}
            {virtualRows.map((track, rowIdx) => (
              <tr
                key={track.id}
                className={selectedId === track.id ? 'lib-row lib-row--selected' : 'lib-row'}
                onClick={() => onSelect(track.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onSelect(track.id)
                  }
                }}
                aria-selected={selectedId === track.id}
                tabIndex={0}
              >
                <td className="lib-col-num">
                  <button
                    type="button"
                    className={`lib-row-play${playingTrackId === track.id ? ' is-playing' : ''}`}
                    onClick={(event) => {
                      event.stopPropagation()
                      onSelect(track.id)
                      onPlay(track.id)
                    }}
                    aria-label={playingTrackId === track.id ? `Pause ${displayValue(track.title, track.filename)}` : `Play ${displayValue(track.title, track.filename)} in persistent player`}
                    title={playingTrackId === track.id ? 'Pause persistent player' : 'Play in persistent player'}
                  >
                    {playingTrackId === track.id ? <Pause size={11} fill="currentColor" /> : <Play size={11} fill="currentColor" />}
                  </button>
                  <span className="lib-row-number">{offset + virtualStart + rowIdx + 1}</span>
                </td>
                <td className="lib-col-title">
                  <strong>{displayValue(track.title, track.filename)}</strong>
                  {track.title && track.title !== track.filename && <span>{track.filename}</span>}
                </td>
                <td>{displayValue(track.artist)}</td>
                <td className="lib-mono">{displayValue(track.bpm)}</td>
                <td className="lib-mono" style={camelotTextColor(track.key_camelot)}>{displayValue(track.key_musical)}</td>
                <td>
                  {track.key_camelot
                    ? <span className="lib-camelot-chip" style={camelotStyle(track.key_camelot)}>{track.key_camelot}</span>
                    : <span className="lib-muted">—</span>}
                </td>
                <td>{displayValue(track.genre)}</td>
                <td><QualityMeter tier={track.quality_tier} /></td>
                <td>
                  <ReviewStatusBadge
                    trackId={track.id}
                    status={reviews[String(track.id)]?.review_status}
                    rating={reviews[String(track.id)]?.rating}
                  />
                </td>
                <td>
                  <div className="lib-status-cell">
                    {track.issues.length === 0
                      ? <CheckCircle2 size={14} className="lib-status-ok" />
                      : <AlertTriangle size={14} className="lib-status-warn" />}
                    <span className="lib-muted">{track.issues.length === 0 ? 'Clean' : `${track.issues.length} issue${track.issues.length > 1 ? 's' : ''}`}</span>
                  </div>
                </td>
              </tr>
            ))}
            {virtualBottomPad > 0 && (
              <tr aria-hidden="true"><td colSpan={10} style={{ height: virtualBottomPad, padding: 0, border: 0 }} /></tr>
            )}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={10} className="lib-empty">
                  {total === 0 ? <div className="lib-empty-import"><strong>No tracks imported yet</strong><span>Set up the library, run a read-only scan preview, then import tracks into CrateIQ’s local index.</span><button className="lib-btn lib-btn--primary lib-btn--sm" type="button" onClick={onOpenImportWizard}>Open Library Setup &amp; Import</button></div> : 'No tracks match the current filters.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      )}
      <div className="lib-pagination">
        <span className="lib-muted">{selectedId ? '1 track selected' : 'No track selected'}</span>
        <button className="lib-btn lib-btn--ghost lib-btn--sm" disabled={offset === 0} onClick={onPrevPage}>
          Prev
        </button>
        <span>{total ? `${offset + 1}-${Math.min(offset + items.length, total)} of ${total}` : '0 tracks'}</span>
        <button className="lib-btn lib-btn--ghost lib-btn--sm" disabled={offset + items.length >= total} onClick={onNextPage}>
          Next
        </button>
      </div>
    </section>
  )
}
