import type { RefObject } from 'react'
import {
  Search,
  ListFilter,
  ArrowUpDown,
  LayoutGrid,
  MoreHorizontal,
  Sparkles,
  RefreshCw,
} from 'lucide-react'
import type { Density, SortKey, SortOrder } from './libraryUtils'

interface Props {
  searchDraft: string
  searchRef: RefObject<HTMLInputElement>
  onSearchChange: (value: string) => void
  activeFilterCount: number
  onToggleFilters: () => void
  sort: SortKey
  order: SortOrder
  onSortChange: (sort: SortKey, order: SortOrder) => void
  density: Density
  onToggleDensity: () => void
  moreMenuOpen: boolean
  moreMenuRef: RefObject<HTMLDivElement>
  onToggleMore: () => void
  onRefresh: () => void
  onOpenAnalysisTools: () => void
}

export default function LibraryToolbar({
  searchDraft,
  searchRef,
  onSearchChange,
  activeFilterCount,
  onToggleFilters,
  sort,
  order,
  onSortChange,
  density,
  onToggleDensity,
  moreMenuOpen,
  moreMenuRef,
  onToggleMore,
  onRefresh,
  onOpenAnalysisTools,
}: Props) {
  return (
    <header className="lib-toolbar">
      <label className="lib-search">
        <Search size={16} />
        <input
          ref={searchRef}
          value={searchDraft}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search artist, title, filename, key, BPM..."
          type="search"
        />
        <kbd className="lib-search-kbd">⌘K</kbd>
      </label>

      <button
        type="button"
        className={`lib-toolbar-btn${activeFilterCount > 0 ? ' lib-toolbar-btn--active' : ''}`}
        onClick={onToggleFilters}
      >
        <ListFilter size={15} />
        Filters
        {activeFilterCount > 0 && <span className="lib-toolbar-badge">{activeFilterCount}</span>}
      </button>

      <label className="lib-toolbar-select">
        <ArrowUpDown size={14} />
        <select
          value={`${sort}:${order}`}
          onChange={(e) => {
            const [nextSort, nextOrder] = e.target.value.split(':') as [SortKey, SortOrder]
            onSortChange(nextSort, nextOrder)
          }}
        >
          <option value="artist:asc">Artist A→Z</option>
          <option value="artist:desc">Artist Z→A</option>
          <option value="title:asc">Title A→Z</option>
          <option value="title:desc">Title Z→A</option>
          <option value="bpm:asc">BPM low→high</option>
          <option value="bpm:desc">BPM high→low</option>
        </select>
      </label>

      <button
        type="button"
        className={`lib-toolbar-btn${density === 'compact' ? ' lib-toolbar-btn--active' : ''}`}
        onClick={onToggleDensity}
        title="Toggle row density"
      >
        <LayoutGrid size={15} />
        View
      </button>

      <div className="lib-toolbar-more" ref={moreMenuRef}>
        <button
          type="button"
          className="lib-toolbar-btn lib-toolbar-btn--icon"
          onClick={onToggleMore}
          title="More"
          aria-label="More actions"
          aria-expanded={moreMenuOpen}
        >
          <MoreHorizontal size={16} />
        </button>
        {moreMenuOpen && (
          <div className="lib-toolbar-menu">
            <button type="button" onClick={onRefresh}>
              <RefreshCw size={12} /> Refresh library
            </button>
          </div>
        )}
      </div>

      <div className="lib-toolbar-spacer" />

      <div className="lib-analyze">
        <button type="button" className="lib-btn lib-btn--primary" onClick={onOpenAnalysisTools}>
          <Sparkles size={15} />
          Analysis &amp; tools
        </button>
      </div>
    </header>
  )
}
