import type { ReactNode } from 'react'
import { RingProgress } from '../library/LibraryOverview'

export type KpiTone = 'emerald' | 'violet' | 'coral' | 'cyan' | 'muted'

interface Props {
  icon?: ReactNode
  tone?: KpiTone
  label: string
  value: ReactNode
  sub?: ReactNode
  /** Renders a ring-progress indicator instead of the icon slot. */
  ring?: { value: number; color: string }
}

/**
 * Shared KPI/overview-stat card. Reuses the exact .lib-overview-card /
 * .lib-overview-icon classes and RingProgress the Library view already
 * defines, so non-Library pages (Quality, BPM Review, Export) get the same
 * card instead of a parallel implementation.
 */
export default function KpiCard({ icon, tone = 'muted', label, value, sub, ring }: Props) {
  return (
    <div className={`lib-card lib-overview-card${ring ? ' lib-overview-card--ring' : ''}`}>
      {ring ? (
        <RingProgress value={ring.value} color={ring.color} />
      ) : icon ? (
        <span className={`lib-overview-icon lib-overview-icon--${tone}`}>{icon}</span>
      ) : null}
      <div className="lib-overview-body">
        <span className="lib-overview-label">{label}</span>
        <strong>{value}</strong>
        {sub && <span className="lib-overview-sub">{sub}</span>}
      </div>
    </div>
  )
}
