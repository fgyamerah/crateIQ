import { useState } from 'react'
import { CheckCircle2, AlertTriangle } from 'lucide-react'
import type { TrackSummary } from '../../types/track'
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
  onSort: (key: SortKey) => void
  onSelect: (id: number) => void
  onPrevPage: () => void
  onNextPage: () => void
}

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
              background: step <= rank ? color : 'rgba(255,255,255,0.08)',
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
  onSort,
  onSelect,
  onPrevPage,
  onNextPage,
}: Props) {
  const [scrollTop, setScrollTop] = useState(0)

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
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {loading && items.length === 0 && (
              Array.from({ length: 8 }).map((_, idx) => (
                <tr key={`lib-skeleton-${idx}`} className="lib-row-skeleton">
                  <td colSpan={9}><span /></td>
                </tr>
              ))
            )}
            {virtualTopPad > 0 && (
              <tr aria-hidden="true"><td colSpan={9} style={{ height: virtualTopPad, padding: 0, border: 0 }} /></tr>
            )}
            {virtualRows.map((track, rowIdx) => (
              <tr
                key={track.id}
                className={selectedId === track.id ? 'lib-row lib-row--selected' : 'lib-row'}
                onClick={() => onSelect(track.id)}
              >
                <td className="lib-col-num">{offset + virtualStart + rowIdx + 1}</td>
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
              <tr aria-hidden="true"><td colSpan={9} style={{ height: virtualBottomPad, padding: 0, border: 0 }} /></tr>
            )}
            {!loading && items.length === 0 && (
              <tr><td colSpan={9} className="lib-empty">No tracks match the current filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>
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
