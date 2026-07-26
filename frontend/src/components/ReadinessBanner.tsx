import { useState } from 'react'
import { AlertTriangle, AlertOctagon, HelpCircle } from 'lucide-react'
import { useReadiness } from '../hooks/useReadiness'
import type { ReadinessCheck, ReadinessStatus } from '../api/runtime'
import StatusStrip from './ui/StatusStrip'

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
      <StatusStrip
        tone="info"
        icon={<HelpCircle size={14} />}
        className="readiness-banner"
        onDismiss={() => setDismissed('fetch-error')}
      >
        Runtime readiness could not be checked.
      </StatusStrip>
    )
  }

  if (!readiness || readiness.status === 'ready') return null
  if (dismissed === readiness.status) return null

  const isNotReady = readiness.status === 'not_ready'
  const checks = topChecks(readiness.checks)

  return (
    <StatusStrip
      tone={isNotReady ? 'danger' : 'warn'}
      role={isNotReady ? 'alert' : 'status'}
      icon={isNotReady ? <AlertOctagon size={14} /> : <AlertTriangle size={14} />}
      className="readiness-banner"
      onDismiss={() => setDismissed(readiness.status)}
      details={checks.map((c) => c.message)}
      footnote={
        <>
          <span>Local diagnostic only — no authentication added.</span>
          <button className="readiness-banner-refresh" onClick={refresh}>
            Recheck
          </button>
        </>
      }
    >
      <strong>{STATUS_LABEL[readiness.status]}</strong>
    </StatusStrip>
  )
}
