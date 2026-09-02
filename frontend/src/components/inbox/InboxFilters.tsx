import { Search, X } from 'lucide-react'
import type { InboxPreparationStatus } from '../../types/track'

export type InboxStatusFilter = InboxPreparationStatus | 'ALL'

const filters: Array<{ value: InboxStatusFilter; label: string }> = [
  { value: 'ALL', label: 'All' },
  { value: 'NEEDS_ATTENTION', label: 'Needs Attention' },
  { value: 'REVIEW', label: 'Review' },
  { value: 'UNSAVED', label: 'Unsaved' },
  { value: 'WRITE_BLOCKED', label: 'Write Blocked' },
  { value: 'READY', label: 'Ready' },
]

interface Props {
  search: string
  onSearchChange: (value: string) => void
  status: InboxStatusFilter
  onStatusChange: (status: InboxStatusFilter) => void
  counts: Partial<Record<InboxStatusFilter, number>>
}

export default function InboxFilters({ search, onSearchChange, status, onStatusChange, counts }: Props) {
  return (
    <section className="inbox-filters" aria-label="Filter Inbox tracks">
      <label className="inbox-search">
        <Search size={15} aria-hidden="true" />
        <span className="lib-visually-hidden">Search all Inbox tracks</span>
        <input
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search track, artist, title, or genre"
          aria-label="Search all Inbox tracks"
        />
        {search && (
          <button type="button" onClick={() => onSearchChange('')} aria-label="Clear Inbox search">
            <X size={14} />
          </button>
        )}
      </label>
      <div className="inbox-status-filters" aria-label="Preparation status">
        {filters.map((filter) => (
          <button
            key={filter.value}
            type="button"
            className={status === filter.value ? 'is-active' : ''}
            aria-pressed={status === filter.value}
            onClick={() => onStatusChange(filter.value)}
          >
            {filter.label} <span>{counts[filter.value] ?? 0}</span>
          </button>
        ))}
      </div>
    </section>
  )
}
