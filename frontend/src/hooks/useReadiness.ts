import { useState, useEffect, useCallback } from 'react'
import { fetchReadiness } from '../api/runtime'
import type { ReadinessResponse } from '../api/runtime'

/**
 * Fetches local-runtime readiness once on mount. No polling — readiness is
 * an environment/config snapshot, not something that changes mid-session.
 * `refresh` lets the banner offer a manual recheck.
 */
export interface UseReadinessResult {
  readiness: ReadinessResponse | null
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useReadiness(): UseReadinessResult {
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchReadiness()
      setReadiness(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not check runtime readiness')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return { readiness, loading, error, refresh: load }
}
