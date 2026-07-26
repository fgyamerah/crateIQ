import { Music2, KeyRound, Wrench as WrenchIcon, Copy } from 'lucide-react'
import type { LibraryOverview as LibraryOverviewData } from '../../api/library'
import { pct } from './libraryUtils'

function RingProgress({ value, size = 34, stroke = 4, color }: { value: number; size?: number; stroke?: number; color: string }) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const offset = c - (Math.max(0, Math.min(100, value)) / 100) * c
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="lib-ring" aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={stroke} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeDasharray={c}
        strokeDashoffset={offset}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: 'stroke-dashoffset 0.4s ease' }}
      />
    </svg>
  )
}

/** Honest qualitative read on how mixable the library is, derived from real key coverage. */
function keyCompatibilityLabel(keyCoveragePct: number, total: number): { label: string; sub: string } {
  if (!total) return { label: '—', sub: 'No tracks scanned yet' }
  if (keyCoveragePct >= 90) return { label: 'Good', sub: 'Most tracks mixable' }
  if (keyCoveragePct >= 70) return { label: 'Fair', sub: 'Some tracks need review' }
  return { label: 'Needs work', sub: 'Many tracks missing key data' }
}

export default function LibraryOverviewCards({ overview }: { overview: LibraryOverviewData | null }) {
  const total = overview?.total_tracks ?? 0
  const bpm = overview?.tracks_with_bpm ?? 0
  const camelot = overview?.tracks_with_camelot_key ?? 0
  const analyzed = overview?.tracks_analyzed ?? 0
  const missingKey = overview ? Math.max(0, total - camelot) : 0
  const bpmPct = pct(bpm, total)
  const keyPct = pct(camelot, total)
  const analyzedPct = pct(analyzed, total)
  const compat = keyCompatibilityLabel(keyPct, total)

  return (
    <>
    <span className="lib-overview-heading">Library Overview</span>
    <div className="lib-overview-grid">
      <div className="lib-card lib-overview-card">
        <span className="lib-overview-icon lib-overview-icon--emerald"><Music2 size={15} /></span>
        <div className="lib-overview-body">
          <span className="lib-overview-label">Total Tracks</span>
          <strong>{total.toLocaleString()}</strong>
          <span className="lib-overview-sub">Read-only DB snapshot</span>
        </div>
      </div>

      <div className="lib-card lib-overview-card lib-overview-card--ring">
        <RingProgress value={analyzedPct} color="var(--brand-emerald)" />
        <div className="lib-overview-body">
          <span className="lib-overview-label">Analyzed</span>
          <strong>{Math.round(analyzedPct)}%</strong>
          <span className="lib-overview-sub">{analyzed.toLocaleString()} with BPM &amp; Key</span>
        </div>
      </div>

      <div className="lib-card lib-overview-card">
        <span className="lib-overview-icon lib-overview-icon--coral"><WrenchIcon size={15} /></span>
        <div className="lib-overview-body">
          <span className="lib-overview-label">Missing Key</span>
          <strong>{overview ? missingKey.toLocaleString() : '—'}</strong>
          <span className="lib-overview-sub">{overview ? `${Math.round(pct(missingKey, total))}% needs review` : 'Not available'}</span>
        </div>
      </div>

      <div className="lib-card lib-overview-card lib-overview-card--ring">
        <RingProgress value={bpmPct} color="var(--brand-cyan)" />
        <div className="lib-overview-body">
          <span className="lib-overview-label">BPM Coverage</span>
          <strong>{Math.round(bpmPct)}%</strong>
          <span className="lib-overview-sub">{bpm.toLocaleString()} tracks</span>
        </div>
      </div>

      <div className="lib-card lib-overview-card">
        <span className="lib-overview-icon lib-overview-icon--violet"><KeyRound size={15} /></span>
        <div className="lib-overview-body">
          <span className="lib-overview-label">Key Compatibility</span>
          <strong>{compat.label}</strong>
          <span className="lib-overview-sub">{compat.sub}</span>
        </div>
      </div>

      <div className="lib-card lib-overview-card">
        <span className="lib-overview-icon lib-overview-icon--muted"><Copy size={15} /></span>
        <div className="lib-overview-body">
          <span className="lib-overview-label">Duplicates</span>
          <strong className="lib-overview-unavailable">Not available</strong>
          <span className="lib-overview-sub">CLI dedupe scan only</span>
        </div>
      </div>
    </div>
    </>
  )
}

export { RingProgress }
export type { LibraryOverviewData }
