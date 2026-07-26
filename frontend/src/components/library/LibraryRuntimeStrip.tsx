import { useState } from 'react'
import { AlertTriangle, AlertOctagon, ChevronDown, ChevronUp, HelpCircle } from 'lucide-react'
import { useReadiness } from '../../hooks/useReadiness'
import type { ReadinessCheck } from '../../api/runtime'

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
 * Compact, single-line runtime diagnostic strip for the Library route —
 * replaces the full-width global ReadinessBanner (see Layout.tsx, which
 * skips rendering that banner on "/"). Same GET /api/runtime/readiness data
 * and same "never blocks the app" diagnostic-only intent, just sized so it
 * doesn't dominate the screen: one line by default, expandable for the
 * top 1-3 issue messages.
 */
export default function LibraryRuntimeStrip() {
  const { readiness, error, refresh } = useReadiness()
  const [expanded, setExpanded] = useState(false)

  if (error) {
    return (
      <div className="lib-runtime-strip lib-runtime-strip--warn" role="status">
        <div className="lib-runtime-strip-row">
          <HelpCircle size={13} />
          <span>Runtime readiness could not be checked.</span>
          <button type="button" className="lib-runtime-strip-recheck" onClick={refresh}>Recheck</button>
        </div>
      </div>
    )
  }

  if (!readiness || readiness.status === 'ready') return null

  const isNotReady = readiness.status === 'not_ready'
  const checks = topChecks(readiness.checks)
  const label = isNotReady ? 'Runtime not ready' : 'Runtime degraded'

  return (
    <div className={`lib-runtime-strip${isNotReady ? ' lib-runtime-strip--not-ready' : ' lib-runtime-strip--warn'}`} role={isNotReady ? 'alert' : 'status'}>
      <div className="lib-runtime-strip-row">
        {isNotReady ? <AlertOctagon size={13} /> : <AlertTriangle size={13} />}
        <strong>{label}</strong>
        {checks.length > 0 && (
          <span className="lib-runtime-strip-first">{checks[0].message}</span>
        )}
        {checks.length > 1 && (
          <button type="button" className="lib-runtime-strip-toggle" onClick={() => setExpanded((v) => !v)}>
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            {expanded ? 'Hide' : `+${checks.length - 1} more`}
          </button>
        )}
        <span className="lib-runtime-strip-spacer" />
        <span className="lib-runtime-strip-note">Local diagnostic only</span>
        <button type="button" className="lib-runtime-strip-recheck" onClick={refresh}>Recheck</button>
      </div>
      {expanded && checks.length > 1 && (
        <ul className="lib-runtime-strip-list">
          {checks.slice(1).map((c) => (
            <li key={c.name}>{c.message}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
