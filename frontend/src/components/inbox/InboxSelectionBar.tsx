interface Props {
  selectedCount: number
  visibleSelectedCount: number
  hiddenSelectedCount: number
  onClear: () => void
  onClearHidden: () => void
}

export default function InboxSelectionBar({
  selectedCount,
  visibleSelectedCount,
  hiddenSelectedCount,
  onClear,
  onClearHidden,
}: Props) {
  if (!selectedCount) return null
  return (
    <div className="inbox-selection-bar" role="status" aria-live="polite">
      <strong>{selectedCount} selected · {visibleSelectedCount} visible</strong>
      {hiddenSelectedCount > 0 && (
        <>
          <span>{hiddenSelectedCount} selected outside this page or filter</span>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClearHidden}>Clear hidden</button>
        </>
      )}
      <button type="button" className="btn btn--ghost btn--sm" onClick={onClear}>Clear selection</button>
    </div>
  )
}
