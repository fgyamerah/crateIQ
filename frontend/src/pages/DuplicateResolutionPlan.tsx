import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ClipboardList, RefreshCw, ShieldCheck } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchDuplicateResolutionPlan } from '../api/duplicateResolutionPlan'
import type { DuplicateResolutionAction, DuplicateResolutionGroup, DuplicateResolutionPlanResponse } from '../types/duplicateResolutionPlan'
import PageHeader from '../components/PageHeader'
import Badge, { type BadgeTone } from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import StatusStrip from '../components/ui/StatusStrip'

function messageFor(error: unknown) {
  return error instanceof ApiError ? error.displayMessage : 'Could not load the duplicate resolution plan.'
}

function actionTone(action: DuplicateResolutionAction): BadgeTone {
  if (action === 'keep') return 'succeeded'
  if (action === 'candidate_for_reversible_resolution') return 'pending'
  if (action === 'review_required') return 'failed'
  return 'info'
}

function actionLabel(action: DuplicateResolutionAction) {
  if (action === 'candidate_for_reversible_resolution') return 'Candidate for reversible resolution'
  if (action === 'review_required') return 'Review required'
  if (action === 'no_action') return 'No action'
  return 'Keep'
}

function blockerLabel(blocker: string) {
  return blocker.replace(/_/g, ' ')
}

function GroupCard({ group }: { group: DuplicateResolutionGroup }) {
  return (
    <article className="resolution-plan-group" key={group.group_id}>
      <div className="resolution-plan-group-heading">
        <strong>{group.group_id}</strong>
        <Badge tone={group.status === 'ready' ? 'succeeded' : 'pending'}>{group.status === 'ready' ? 'Ready' : 'Blocked'}</Badge>
        <span className="resolution-plan-group-meta">match basis: {group.match_basis}{group.checksum_prefix ? ` (${group.checksum_prefix}…)` : ''}</span>
      </div>
      {group.blockers.length > 0 && (
        <p className="resolution-plan-group-blockers">Blocked: {group.blockers.map(blockerLabel).join(', ')}</p>
      )}
      <div className="resolution-plan-items">
        {group.items.map((item) => (
          <div key={item.action_id} className="resolution-plan-item">
            <Badge tone={actionTone(item.action)}>{actionLabel(item.action)}</Badge>
            <span>{item.filename}</span>
            {item.relative_path && <code>{item.relative_path}</code>}
            {item.blockers.length > 0 && (
              <span className="resolution-plan-item-blockers">{item.blockers.map(blockerLabel).join(', ')}</span>
            )}
            {item.execution_requirements && (
              <p className="resolution-plan-item-note">
                Identity evidence: {item.execution_requirements.identity_evidence.type === 'checksum_prefix'
                  ? `${item.execution_requirements.identity_evidence.value}… (prefix only, not a full hash)`
                  : 'none retained'}. A hash-verified backup and reversible destination would be required before any future action — no apply endpoint exists yet.
              </p>
            )}
          </div>
        ))}
      </div>
    </article>
  )
}

export default function DuplicateResolutionPlan() {
  const [plan, setPlan] = useState<DuplicateResolutionPlanResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try { setPlan(await fetchDuplicateResolutionPlan()) } catch (err) { setError(messageFor(err)) } finally { setLoading(false) }
  }, [])

  useEffect(() => { void load() }, [load])

  const summary = plan?.summary

  return <main className="page duplicates-page">
    <PageHeader
      title="Duplicate Resolution Plan"
      subtitle="Plan only — no files changed. Derived read-only from the latest saved Duplicate Review snapshot and its human decisions."
      actions={<>
        <Link className="btn btn--ghost btn--sm" to="/duplicates">Duplicate Review</Link>
        <button className="btn btn--primary btn--sm" disabled={loading} onClick={() => void load()}><RefreshCw size={14} /> {loading ? 'Loading…' : 'Refresh plan'}</button>
      </>}
    />

    <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>Plan only — no files changed. No apply/execute endpoint exists for duplicate resolution.</StatusStrip>
    <div className="duplicates-policy-chips" aria-label="Duplicate resolution plan safety policies">
      <span><ShieldCheck size={13} /> Plan only</span><span>No apply endpoint</span><span>No delete</span><span>No move</span><span>No quarantine</span><span>No tag writes</span>
    </div>

    <section className="duplicates-kpis" aria-label="Duplicate resolution plan summary">
      <KpiCard tone="cyan" label="Groups" value={summary?.groups ?? '—'} sub="From the latest saved review snapshot" />
      <KpiCard tone="emerald" label="Ready" value={summary?.ready_groups ?? '—'} sub="Unambiguous evidence + decisions" />
      <KpiCard tone="coral" label="Blocked" value={summary?.blocked_groups ?? '—'} sub="Needs review or evidence is insufficient" />
      <KpiCard tone="violet" label="Keep" value={summary?.keep ?? '—'} sub="Explicit keeper, no mutation" />
      <KpiCard tone="cyan" label="Candidates" value={summary?.candidate_for_reversible_resolution ?? '—'} sub="Future reversible resolution only" />
      <KpiCard tone="coral" label="Review required" value={summary?.review_required ?? '—'} sub="Group is blocked" />
    </section>

    {error && <StatusStrip tone="danger">{error}</StatusStrip>}
    {plan?.message && <StatusStrip tone="info">{plan.message}</StatusStrip>}
    {plan?.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}

    {loading ? <p className="muted">Loading duplicate resolution plan…</p> : !plan || plan.groups.length === 0 ? (
      <EmptyState
        icon={<ClipboardList size={28} />}
        title="No resolution plan to show"
        message="Save a duplicate preview and record review decisions on the Duplicate Review page first. This plan never runs a preview or writes a decision itself."
        action={<Link className="btn btn--primary btn--sm" to="/duplicates">Open Duplicate Review</Link>}
      />
    ) : (
      <div className="resolution-plan-list">
        {plan.groups.map((group) => <GroupCard key={group.group_id} group={group} />)}
      </div>
    )}
  </main>
}
