import type { ReactNode } from 'react'

export type BadgeTone = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'info'

interface Props {
  tone: BadgeTone
  children: ReactNode
}

/**
 * Shared semantic badge for non-job-status labels (Camelot keys, vibes,
 * ledger/reconciliation states, confidence levels). For job status specifically,
 * prefer StatusBadge, which also supplies the label text.
 */
export default function Badge({ tone, children }: Props) {
  return <span className={`badge badge--${tone}`}>{children}</span>
}
