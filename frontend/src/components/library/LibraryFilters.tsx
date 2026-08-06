import { ListFilter, X } from 'lucide-react'
import type { LibraryOverview } from '../../api/library'
import type { LibraryUiState } from './libraryUtils'

interface Props {
  overview: LibraryOverview | null
  ui: LibraryUiState
  expanded: boolean
  onChange: (updater: (current: LibraryUiState) => LibraryUiState) => void
  onToggleExpanded: () => void
}

/**
 * Functional filter row for the Library route. Every chip maps 1:1 to a
 * query param GET /api/tracks already accepts — genre, bpm_min/bpm_max,
 * has_key. Camelot-range/energy/source filters from the mockup have no
 * backing field on TrackSummary yet, so they're omitted rather than wired
 * to a no-op control.
 */
export default function LibraryFilters({ overview, ui, expanded, onChange, onToggleExpanded }: Props) {
  const topGenres = (overview?.genre_top_counts ?? []).slice(0, 8)
  const activePills: Array<{ key: string; label: string; onClear: () => void }> = []
  if (ui.genreFilter) {
    activePills.push({
      key: 'genre',
      label: `Genre: ${ui.genreFilter}`,
      onClear: () => onChange((c) => ({ ...c, genreFilter: '', offset: 0 })),
    })
  }
  if (ui.bpmMinFilter || ui.bpmMaxFilter) {
    activePills.push({
      key: 'bpm',
      label: `BPM: ${ui.bpmMinFilter || '0'} – ${ui.bpmMaxFilter || '∞'}`,
      onClear: () => onChange((c) => ({ ...c, bpmMinFilter: '', bpmMaxFilter: '', offset: 0 })),
    })
  }
  if (ui.hasKeyFilter) {
    activePills.push({
      key: 'key',
      label: ui.hasKeyFilter === 'yes' ? 'Key: Has key' : 'Key: Missing key',
      onClear: () => onChange((c) => ({ ...c, hasKeyFilter: '', offset: 0 })),
    })
  }
  const hasAnyFilter = activePills.length > 0

  function clearAll() {
    onChange((c) => ({ ...c, genreFilter: '', bpmMinFilter: '', bpmMaxFilter: '', hasKeyFilter: '', offset: 0 }))
  }

  return (
    <div className="lib-filter-bar">
      {hasAnyFilter && (
        <div className="lib-filter-pills">
          {activePills.map((pill) => (
            <span key={pill.key} className="lib-pill lib-pill--active">
              {pill.label}
              <button type="button" onClick={pill.onClear} aria-label={`Clear ${pill.label}`}>
                <X size={11} />
              </button>
            </span>
          ))}
          <button type="button" className="lib-pill lib-pill--clear" onClick={clearAll}>
            Clear all
          </button>
        </div>
      )}
      {expanded && (
        <div className="lib-filter-groups">
          <div className="lib-filter-group">
            <span className="lib-filter-group-label">Genre</span>
            {topGenres.length === 0 && <span className="lib-muted">No genre data</span>}
            {topGenres.map((g) => (
              <button
                key={g.genre}
                type="button"
                className={`lib-chip${ui.genreFilter === g.genre ? ' lib-chip--active' : ''}`}
                onClick={() => onChange((c) => ({ ...c, genreFilter: c.genreFilter === g.genre ? '' : g.genre, offset: 0 }))}
              >
                {g.genre} <span className="lib-chip-count">{g.count}</span>
              </button>
            ))}
          </div>
          <div className="lib-filter-group">
            <span className="lib-filter-group-label">BPM</span>
            <input
              className="lib-filter-input"
              type="number"
              inputMode="numeric"
              placeholder="min"
              value={ui.bpmMinFilter}
              onChange={(e) => onChange((c) => ({ ...c, bpmMinFilter: e.target.value, offset: 0 }))}
            />
            <span className="lib-filter-group-sep">–</span>
            <input
              className="lib-filter-input"
              type="number"
              inputMode="numeric"
              placeholder="max"
              value={ui.bpmMaxFilter}
              onChange={(e) => onChange((c) => ({ ...c, bpmMaxFilter: e.target.value, offset: 0 }))}
            />
          </div>
          <div className="lib-filter-group">
            <span className="lib-filter-group-label">Key</span>
            <button
              type="button"
              className={`lib-chip${ui.hasKeyFilter === 'yes' ? ' lib-chip--active' : ''}`}
              onClick={() => onChange((c) => ({ ...c, hasKeyFilter: c.hasKeyFilter === 'yes' ? '' : 'yes', offset: 0 }))}
            >
              Has key
            </button>
            <button
              type="button"
              className={`lib-chip${ui.hasKeyFilter === 'no' ? ' lib-chip--active' : ''}`}
              onClick={() => onChange((c) => ({ ...c, hasKeyFilter: c.hasKeyFilter === 'no' ? '' : 'no', offset: 0 }))}
            >
              Missing key
            </button>
          </div>
        </div>
      )}
      {!hasAnyFilter && !expanded && (
        <button type="button" className="lib-filter-bar-hint" onClick={onToggleExpanded}>
          <ListFilter size={12} /> Add a filter
        </button>
      )}
    </div>
  )
}
