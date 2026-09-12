interface Props {
  selectedCount: number
  visibleSelectedCount: number
  hiddenSelectedCount: number
  bulkLimit?: number
  onClear: () => void
  onClearHidden: () => void
  onAddToPlaylist?: () => void
}

export default function InboxSelectionBar({
  selectedCount,
  visibleSelectedCount,
  hiddenSelectedCount,
  bulkLimit,
  onClear,
  onClearHidden,
  onAddToPlaylist,
}: Props) {
  if (!selectedCount) return null
  return (
    <div className="inbox-selection-bar" role="status" aria-live="polite">
      <strong>{selectedCount} selected · {visibleSelectedCount} visible</strong>
      {bulkLimit && <span>Bulk Review and Edit Metadata: max {bulkLimit}</span>}
      {hiddenSelectedCount > 0 && (
        <>
          <span>{hiddenSelectedCount} selected outside this page or filter</span>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClearHidden}>Clear hidden</button>
        </>
      )}
      {onAddToPlaylist && <button type="button" className="btn btn--primary btn--sm" onClick={onAddToPlaylist}>Add to Playlist</button>}
      <button type="button" className="btn btn--ghost btn--sm" onClick={onClear}>Clear selection</button>
    </div>
  )
}
