import { useState } from 'react'
import { AlertTriangle, AlertOctagon, HelpCircle } from 'lucide-react'
import { useReadiness } from '../hooks/useReadiness'
import type { ReadinessCheck, ReadinessStatus } from '../api/runtime'

const STATUS_LABEL: Record<ReadinessStatus, string> = {
  ready: 'Runtime ready',
  degraded: 'Runtime degraded',
  not_ready: 'Runtime not ready',
}

/** Required fails first, then other fails, then warns — worst news first. */
function topChecks(checks: ReadinessCheck[]): ReadinessCheck[] {
  const notPassing = checks.filter((c) => c.status !== 'pass')
  const rank = (c: ReadinessCheck) => {
    if (c.status === 'fail') return c.required ? 0 : 1
    return 2
  }
  return [...notPassing].sort((a, b) => rank(a) - rank(b)).slice(0, 3)
}

/**
 * Small, dismissible banner surfacing GET /api/runtime/readiness.
 * Diagnostic only: it never blocks the app and never renders raw check
 * metadata (which may include local paths).
 */
export default function ReadinessBanner() {
  const { readiness, error, refresh } = useReadiness()
  const [dismissed, setDismissed] = useState<string | null>(null)

  if (error) {
    if (dismissed === 'fetch-error') return null
    return (
      <div className="crate-warning-banner readiness-banner" role="status">
        <div className="readiness-banner-row">
          <HelpCircle size={14} />
          <span>Runtime readiness could not be checked.</span>
          <button
            className="error-banner-dismiss"
            onClick={() => setDismissed('fetch-error')}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      </div>
    )
  }

  if (!readiness || readiness.status === 'ready') return null
  if (dismissed === readiness.status) return null

  const isNotReady = readiness.status === 'not_ready'
  const checks = topChecks(readiness.checks)

  return (
    <div
      className={isNotReady ? 'error-banner readiness-banner' : 'crate-warning-banner readiness-banner'}
      role={isNotReady ? 'alert' : 'status'}
    >
      <div className="readiness-banner-row">
        {isNotReady ? <AlertOctagon size={14} /> : <AlertTriangle size={14} />}
        <strong>{STATUS_LABEL[readiness.status]}</strong>
        <button
          className="error-banner-dismiss"
          onClick={() => setDismissed(readiness.status)}
          aria-label="Dismiss"
        >
          ×
        </button>
      </div>

      {checks.length > 0 && (
        <ul className="readiness-banner-checks">
          {checks.map((c) => (
            <li key={c.name}>{c.message}</li>
          ))}
        </ul>
      )}

      <div className="readiness-banner-footnote">
        <span>Local diagnostic only — no authentication added.</span>
        <button className="readiness-banner-refresh" onClick={refresh}>
          Recheck
        </button>
      </div>
    </div>
  )
}
