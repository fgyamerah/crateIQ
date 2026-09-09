import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

export function useInboxSelection(visibleIds: number[], availableIds: number[] | null) {
  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => new Set())
  const anchorIdRef = useRef<number | null>(null)

  useEffect(() => {
    if (availableIds === null) return
    const available = new Set(availableIds)
    setSelectedIds((current) => {
      const next = new Set(Array.from(current).filter((id) => available.has(id)))
      return next.size === current.size ? current : next
    })
  }, [availableIds])

  const visibleSelectedCount = useMemo(
    () => visibleIds.reduce((count, id) => count + (selectedIds.has(id) ? 1 : 0), 0),
    [selectedIds, visibleIds],
  )
  const hiddenSelectedCount = selectedIds.size - visibleSelectedCount
  const allVisibleSelected = visibleIds.length > 0 && visibleSelectedCount === visibleIds.length

  const toggle = useCallback((trackId: number, range = false) => {
    setSelectedIds((current) => {
      const next = new Set(current)
      const shouldSelect = !current.has(trackId)
      const anchorIndex = anchorIdRef.current === null ? -1 : visibleIds.indexOf(anchorIdRef.current)
      const targetIndex = visibleIds.indexOf(trackId)
      if (range && anchorIndex >= 0 && targetIndex >= 0) {
        const [start, end] = anchorIndex < targetIndex
          ? [anchorIndex, targetIndex]
          : [targetIndex, anchorIndex]
        visibleIds.slice(start, end + 1).forEach((id) => {
          if (shouldSelect) next.add(id)
          else next.delete(id)
        })
      } else if (shouldSelect) {
        next.add(trackId)
      } else {
        next.delete(trackId)
      }
      return next
    })
    anchorIdRef.current = trackId
  }, [visibleIds])

  const toggleVisible = useCallback(() => {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (visibleIds.length > 0 && visibleIds.every((id) => current.has(id))) {
        visibleIds.forEach((id) => next.delete(id))
      } else {
        visibleIds.forEach((id) => next.add(id))
      }
      return next
    })
  }, [visibleIds])

  const clear = useCallback(() => {
    setSelectedIds(new Set())
    anchorIdRef.current = null
  }, [])

  const clearHidden = useCallback(() => {
    const visible = new Set(visibleIds)
    setSelectedIds((current) => new Set(Array.from(current).filter((id) => visible.has(id))))
  }, [visibleIds])

  return {
    selectedIds,
    selectedCount: selectedIds.size,
    visibleSelectedCount,
    hiddenSelectedCount,
    allVisibleSelected,
    toggle,
    toggleVisible,
    clear,
    clearHidden,
  }
}
