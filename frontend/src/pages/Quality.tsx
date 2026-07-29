import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Music2, KeyRound } from 'lucide-react'
import { fetchLibraryQuality } from '../api/libraryQuality'
import type { LibraryQualityResponse } from '../types/libraryQuality'
import KpiCard from '../components/ui/KpiCard'
import EmptyState from '../components/ui/EmptyState'
import Badge, { type BadgeTone } from '../components/ui/Badge'

function clamp(value: number, min = 0, max = 100): number {
  return Math.max(min, Math.min(max, value))
}

function pct(value: number, total: number): string {
  if (!total) return '0%'
  return `${Math.round((value / total) * 100)}%`
}

function pctValue(value: number, total: number): number {
  if (!total) return 0
  return clamp((value / total) * 100)
}

function scoreFromQuality(data: LibraryQualityResponse | null): number {
  if (!data || !data.total_tracks) return 100
  const missing = data.issues_by_type.missing_artist + data.issues_by_type.missing_title
  const suspicious = data.issues_by_type.suspicious_artist + data.issues_by_type.suspicious_title
  const weak = data.issues_by_type.weak_filename_parse
  return clamp(Math.round(100 - (missing * 8 + suspicious * 4 + weak * 2)))
}

/** HIGH/MEDIUM/LOW confidence -> Badge tone. Reuses the same succeeded/
 *  pending/failed color tokens the confidence chips already carried
 *  conceptually (green/amber/red), via the shared Badge primitive. */
const CONFIDENCE_TONE: Record<'HIGH' | 'MEDIUM' | 'LOW', BadgeTone> = {
  HIGH: 'succeeded',
  MEDIUM: 'pending',
  LOW: 'failed',
}

function SectionCard({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: string
  children: ReactNode
}) {
  return (
    <section className="crate-panel quality-section">
      <div className="crate-panel-head">
        <div>
          <h2>{title}</h2>
          {subtitle && <div className="quality-section-subtitle">{subtitle}</div>}
        </div>
      </div>
      <div className="quality-section-body">{children}</div>
    </section>
  )
}

export default function Quality() {
  const [quality, setQuality] = useState<LibraryQualityResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function refresh() {
    setLoading(true)
    setError(null)
    try {
      setQuality(await fetchLibraryQuality())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load library quality dashboard')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const score = useMemo(() => scoreFromQuality(quality), [quality])
  const total = quality?.total_tracks ?? 0
  const repair = quality?.metadata_repair
  const sanitation = quality?.metadata_sanitation
  const coverage = quality?.coverage
  const issues = quality?.issues_by_type

  return (
    <main className="quality-page">
      <header className="quality-hero crate-panel">
        <div className="quality-hero-copy">
          <span className="crate-kicker">Library Quality</span>
          <h1>Cleanup progress, issue backlog, and next actions</h1>
          <p>
            Read-only operational snapshot of the library health, metadata coverage, and queued cleanup work.
          </p>
        </div>
        <div className="quality-score-card">
          <span className="crate-metric-label">Health score</span>
          <strong>{score}</strong>
          <span className="crate-metric-sub">
            {loading ? 'Refreshing snapshot' : `${total.toLocaleString()} tracks evaluated`}
          </span>
          <div className="crate-meter">
            <span style={{ width: `${score}%` }} />
          </div>
        </div>
      </header>

      {error && <div className="metadata-repair-error">{error}</div>}

      <section className="crate-card-grid quality-top-grid">
        <KpiCard
          icon={<Music2 size={15} />}
          tone="emerald"
          label="Total tracks"
          value={loading ? '...' : total.toLocaleString()}
          sub="Read-only DB snapshot"
        />
        <KpiCard
          ring={{ value: pctValue(quality?.issue_total ?? 0, total), color: 'var(--brand-coral)' }}
          label="Issue total"
          value={loading ? '...' : (quality?.issue_total ?? 0).toLocaleString()}
          sub="Remaining cleanup targets"
        />
        <KpiCard
          ring={{ value: pctValue(coverage?.with_artist ?? 0, total), color: 'var(--brand-emerald)' }}
          label="Artist coverage"
          value={loading ? '...' : pct(coverage?.with_artist ?? 0, total)}
          sub={`${coverage?.with_artist ?? 0} tracks with artist`}
        />
        <KpiCard
          ring={{ value: pctValue(coverage?.with_title ?? 0, total), color: 'var(--brand-cyan)' }}
          label="Title coverage"
          value={loading ? '...' : pct(coverage?.with_title ?? 0, total)}
          sub={`${coverage?.with_title ?? 0} tracks with title`}
        />
        <KpiCard
          icon={<KeyRound size={15} />}
          tone="violet"
          label="BPM / Camelot / Genre"
          value={loading ? '...' : `${pct(coverage?.with_bpm ?? 0, total)} / ${pct(coverage?.with_camelot ?? 0, total)} / ${pct(coverage?.with_genre ?? 0, total)}`}
          sub="Metadata completeness"
        />
      </section>

      <section className="quality-grid">
        <SectionCard title="Issue Counts" subtitle="The backlog currently visible in the Issues view.">
          <div className="quality-pill-grid">
            <div className="quality-pill">
              <span>Missing artist</span>
              <strong>{issues?.missing_artist ?? 0}</strong>
            </div>
            <div className="quality-pill">
              <span>Missing title</span>
              <strong>{issues?.missing_title ?? 0}</strong>
            </div>
            <div className="quality-pill">
              <span>Suspicious artist</span>
              <strong>{issues?.suspicious_artist ?? 0}</strong>
            </div>
            <div className="quality-pill">
              <span>Suspicious title</span>
              <strong>{issues?.suspicious_title ?? 0}</strong>
            </div>
            <div className="quality-pill">
              <span>Weak parse</span>
              <strong>{issues?.weak_filename_parse ?? 0}</strong>
            </div>
          </div>
        </SectionCard>

        <SectionCard title="Repair Queue Status" subtitle="Metadata Repair state and confidence mix.">
          <div className="quality-queue-stats">
            <div><span>Queue</span><strong>{repair?.queue_total ?? 0}</strong></div>
            <div><span>Pending</span><strong>{repair?.pending ?? 0}</strong></div>
            <div><span>Approved</span><strong>{repair?.approved ?? 0}</strong></div>
            <div><span>Partial</span><strong>{repair?.partial ?? 0}</strong></div>
            <div><span>Applied</span><strong>{repair?.applied ?? 0}</strong></div>
            <div><span>No-op</span><strong>{repair?.no_op ?? 0}</strong></div>
          </div>
          <div className="quality-confidence-row">
            {(['HIGH', 'MEDIUM', 'LOW'] as const).map((key) => (
              <Badge key={key} tone={CONFIDENCE_TONE[key]}>
                {key} {repair?.by_confidence?.[key] ?? 0}
              </Badge>
            ))}
          </div>
          <Link className="quality-inline-link" to="/metadata-repair">Open Metadata Repair</Link>
        </SectionCard>

        <SectionCard title="Sanitation Queue Status" subtitle="Metadata Sanitation state and confidence mix.">
          <div className="quality-queue-stats">
            <div><span>Queue</span><strong>{sanitation?.queue_total ?? 0}</strong></div>
            <div><span>Pending</span><strong>{sanitation?.pending ?? 0}</strong></div>
            <div><span>Approved</span><strong>{sanitation?.approved ?? 0}</strong></div>
            <div><span>Partial</span><strong>{sanitation?.partial ?? 0}</strong></div>
            <div><span>Applied</span><strong>{sanitation?.applied ?? 0}</strong></div>
            <div><span>No-op</span><strong>{sanitation?.no_op ?? 0}</strong></div>
          </div>
          <div className="quality-confidence-row">
            {(['HIGH', 'MEDIUM', 'LOW'] as const).map((key) => (
              <Badge key={key} tone={CONFIDENCE_TONE[key]}>
                {key} {sanitation?.by_confidence?.[key] ?? 0}
              </Badge>
            ))}
          </div>
          <Link className="quality-inline-link" to="/metadata-sanitation">Open Metadata Sanitation</Link>
        </SectionCard>
      </section>

      <SectionCard title="Recommended Next Actions" subtitle="Prioritized cleanup actions from the current snapshot.">
        {!loading && (quality?.recommended_next_actions?.length ?? 0) === 0 ? (
          <EmptyState message="No recommended actions right now — the library looks clean." />
        ) : (
          <div className="quality-actions">
            {(quality?.recommended_next_actions || []).map((action) => (
              <Link key={`${action.target}-${action.label}`} to={action.target} className="quality-action-card">
                <strong>{action.label}</strong>
                <span>{action.reason}</span>
              </Link>
            ))}
          </div>
        )}
      </SectionCard>

      <SectionCard title="Cleanup Progress" subtitle="Operational shortcuts to the main cleanup screens.">
        <div className="quality-nav-grid">
          <Link to="/" className="quality-nav-card">
            <strong>Library</strong>
            <span>Browse the canonical track table and coverage snapshots.</span>
          </Link>
          <Link to="/issues" className="quality-nav-card">
            <strong>Issues</strong>
            <span>Review remaining missing and suspicious metadata.</span>
          </Link>
          <Link to="/metadata-repair" className="quality-nav-card">
            <strong>Metadata Repair</strong>
            <span>Apply filename-driven repair proposals.</span>
          </Link>
          <Link to="/metadata-sanitation" className="quality-nav-card">
            <strong>Metadata Sanitation</strong>
            <span>Clean contamination and suspicious suffixes.</span>
          </Link>
        </div>
      </SectionCard>
    </main>
  )
}
