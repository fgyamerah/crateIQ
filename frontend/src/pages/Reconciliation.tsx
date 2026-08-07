import type { ReactNode } from 'react'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Archive, CopyCheck, FileQuestion, FileX2, Loader2, RefreshCw, ShieldCheck, Workflow } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchDuplicateReview } from '../api/duplicates'
import {
  fetchReconciliationFindings,
  fetchReconciliationLedger,
  fetchReconciliationLedgerEntry,
  fetchReconciliationQuarantine,
  proposeReconciliationPlan,
  validateReconciliationPlan,
} from '../api/reconciliation'
import type { DuplicateReviewSummary } from '../types/duplicates'
import type {
  FindingType,
  QuarantineListingResponse,
  ReconciliationFinding,
  ReconciliationFindingsResponse,
  ReconciliationLedgerEntry,
  ReconciliationPlanProposeResponse,
  ReconciliationPlanValidationResult,
} from '../types/reconciliation'
import PageHeader from '../components/PageHeader'
import Badge, { type BadgeTone } from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import StatusStrip from '../components/ui/StatusStrip'

function messageFor(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.displayMessage : error instanceof Error ? error.message : fallback
}

function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

function formatBytes(value: number | null): string {
  if (value == null) return 'Size unavailable'
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function parseTableList(value: string | null): string[] {
  if (!value) return []
  try {
    const parsed = JSON.parse(value)
    if (Array.isArray(parsed)) return parsed.map((item) => String(item)).filter((item) => item.trim())
  } catch {
    // fall through to comma-separated fallback
  }
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

function prettyJson(value: string | null): string {
  if (!value) return '—'
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}

function statusTone(status: string | null): BadgeTone {
  const value = (status || 'unknown').toLowerCase()
  if (value.includes('fail') || value.includes('error') || value === 'invalid') return 'failed'
  if (value.includes('pend') || value.includes('queue')) return 'pending'
  if (value.includes('ok') || value.includes('success') || value.includes('applied') || value.includes('done') || value === 'valid') {
    return 'succeeded'
  }
  return 'info'
}

function LedgerBadge({ status }: { status: string | null }) {
  return <Badge tone={statusTone(status)}>{status || 'unknown'}</Badge>
}

function DetailField({ label, value }: { label: string; value: ReactNode }) {
  return <><dt>{label}</dt><dd>{value}</dd></>
}

const FINDING_LABELS: Record<FindingType, string> = {
  indexed_missing_file: 'Missing file',
  stale_path: 'Stale path',
  untracked_file: 'Untracked file',
  path_candidate: 'Path candidate',
}

function FindingsList({ findings, selectedId, onSelect }: { findings: ReconciliationFinding[]; selectedId: string | null; onSelect: (id: string) => void }) {
  return <div className="reconciliation-findings-scroll">
    {findings.map((finding) => {
      const side = finding.db_side ?? finding.filesystem_side
      return <button
        key={finding.finding_id}
        type="button"
        className={`reconciliation-findings-option${selectedId === finding.finding_id ? ' is-selected' : ''}`}
        onClick={() => onSelect(finding.finding_id)}
      >
        <span><strong>{side?.relative_path || side?.filename || finding.finding_id}</strong><small>{FINDING_LABELS[finding.finding_type]}</small></span>
        <span><Badge tone="pending">{finding.finding_type}</Badge></span>
      </button>
    })}
  </div>
}

function FindingDetail({ finding }: { finding: ReconciliationFinding | null }) {
  if (!finding) return <EmptyState message="Select a finding to inspect its evidence." />
  const evidenceEntries = Object.entries(finding.evidence).filter(([, value]) => value !== null && value !== undefined && value !== '')
  return <div className="recon-detail">
    <p className="muted">{finding.summary}</p>
    <div className="reconciliation-findings-sides">
      <div className="reconciliation-findings-side">
        <h4>DB / index side</h4>
        {finding.db_side ? <>
          <code>{finding.db_side.relative_path || '—'}</code>
          {finding.db_side.status && <p>Status: {finding.db_side.status}</p>}
          {finding.db_side.stage && <p>Stage: {finding.db_side.stage}</p>}
          {finding.db_side.size_bytes != null && <p>{formatBytes(finding.db_side.size_bytes)}</p>}
        </> : <p>No indexed row on this side.</p>}
      </div>
      <div className="reconciliation-findings-side">
        <h4>Filesystem side</h4>
        {finding.filesystem_side ? <>
          <code>{finding.filesystem_side.relative_path || '—'}</code>
          {finding.filesystem_side.size_bytes != null && <p>{formatBytes(finding.filesystem_side.size_bytes)}</p>}
        </> : <p>No file on this side.</p>}
      </div>
    </div>
    {evidenceEntries.length > 0 && <dl className="def-list reconciliation-findings-evidence">
      {evidenceEntries.map(([key, value]) => <DetailField key={key} label={key} value={String(value)} />)}
    </dl>}
    <StatusStrip tone="info">Evidence only. No next action here changes a file, tag, or database row.</StatusStrip>
  </div>
}

type WorkspaceTab = 'duplicates' | 'missing' | 'untracked' | 'quarantine' | 'plans'

export default function Reconciliation() {
  const [tab, setTab] = useState<WorkspaceTab>('duplicates')

  const [dupSummary, setDupSummary] = useState<DuplicateReviewSummary | null>(null)
  const [dupMessage, setDupMessage] = useState<string | null>(null)
  const [dupLoading, setDupLoading] = useState(true)
  const [dupError, setDupError] = useState<string | null>(null)

  const [findings, setFindings] = useState<ReconciliationFindingsResponse | null>(null)
  const [findingsLoading, setFindingsLoading] = useState(true)
  const [findingsError, setFindingsError] = useState<string | null>(null)
  const [selectedMissingId, setSelectedMissingId] = useState<string | null>(null)
  const [selectedUntrackedId, setSelectedUntrackedId] = useState<string | null>(null)

  const [quarantine, setQuarantine] = useState<QuarantineListingResponse | null>(null)
  const [quarantineLoading, setQuarantineLoading] = useState(true)
  const [quarantineError, setQuarantineError] = useState<string | null>(null)

  const [entries, setEntries] = useState<ReconciliationLedgerEntry[]>([])
  const [selectedLedgerId, setSelectedLedgerId] = useState<string | null>(null)
  const [selectedLedgerEntry, setSelectedLedgerEntry] = useState<ReconciliationLedgerEntry | null>(null)
  const [loadingLedgerList, setLoadingLedgerList] = useState(true)
  const [loadingLedgerDetail, setLoadingLedgerDetail] = useState(false)
  const [ledgerError, setLedgerError] = useState<string | null>(null)

  const [proposal, setProposal] = useState<ReconciliationPlanProposeResponse | null>(null)
  const [proposing, setProposing] = useState(false)
  const [proposeError, setProposeError] = useState<string | null>(null)
  const [validation, setValidation] = useState<ReconciliationPlanValidationResult | null>(null)
  const [loadingValidation, setLoadingValidation] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  async function loadDuplicates() {
    setDupLoading(true)
    setDupError(null)
    try {
      const review = await fetchDuplicateReview()
      setDupSummary(review.summary)
      setDupMessage(review.message)
    } catch (err) {
      setDupError(messageFor(err, 'Could not load duplicate review summary.'))
    } finally {
      setDupLoading(false)
    }
  }

  async function loadFindings() {
    setFindingsLoading(true)
    setFindingsError(null)
    try {
      const result = await fetchReconciliationFindings()
      setFindings(result)
    } catch (err) {
      setFindingsError(messageFor(err, 'Could not load reconciliation findings.'))
    } finally {
      setFindingsLoading(false)
    }
  }

  async function loadQuarantine() {
    setQuarantineLoading(true)
    setQuarantineError(null)
    try {
      setQuarantine(await fetchReconciliationQuarantine())
    } catch (err) {
      setQuarantineError(messageFor(err, 'Could not load the quarantine listing.'))
    } finally {
      setQuarantineLoading(false)
    }
  }

  async function loadLedger() {
    setLoadingLedgerList(true)
    setLedgerError(null)
    try {
      const rows = await fetchReconciliationLedger()
      setEntries(rows)
      setSelectedLedgerId((current) => (current && rows.some((entry) => entry.ledger_id === current)) ? current : rows[0]?.ledger_id ?? null)
      if (!rows.length) setSelectedLedgerEntry(null)
    } catch (err) {
      setLedgerError(messageFor(err, 'Could not load the reconciliation ledger.'))
    } finally {
      setLoadingLedgerList(false)
    }
  }

  async function proposePlan() {
    setProposing(true)
    setProposeError(null)
    try {
      setProposal(await proposeReconciliationPlan())
      setValidation(null)
    } catch (err) {
      setProposeError(messageFor(err, 'Could not propose a reconciliation plan.'))
    } finally {
      setProposing(false)
    }
  }

  async function validateLatestPlan() {
    setLoadingValidation(true)
    setValidationError(null)
    try {
      setValidation(await validateReconciliationPlan({ latest: true }))
    } catch (err) {
      setValidationError(messageFor(err, 'Could not validate the latest plan.'))
    } finally {
      setLoadingValidation(false)
    }
  }

  useEffect(() => { void loadDuplicates() }, [])
  useEffect(() => { void loadFindings() }, [])
  useEffect(() => { void loadQuarantine() }, [])
  useEffect(() => { void loadLedger() }, [])

  useEffect(() => {
    if (!selectedLedgerId) return
    let cancelled = false
    setLoadingLedgerDetail(true)
    void fetchReconciliationLedgerEntry(selectedLedgerId)
      .then((entry) => { if (!cancelled) setSelectedLedgerEntry(entry) })
      .catch((err) => { if (!cancelled) setLedgerError(messageFor(err, 'Could not load ledger entry.')) })
      .finally(() => { if (!cancelled) setLoadingLedgerDetail(false) })
    return () => { cancelled = true }
  }, [selectedLedgerId])

  const missingFindings = useMemo(
    () => findings?.findings.filter((f) => f.finding_type === 'indexed_missing_file' || f.finding_type === 'stale_path') ?? [],
    [findings],
  )
  const untrackedFindings = useMemo(
    () => findings?.findings.filter((f) => f.finding_type === 'untracked_file') ?? [],
    [findings],
  )
  const candidateCount = findings?.summary.path_candidate ?? 0
  const selectedMissing = missingFindings.find((f) => f.finding_id === selectedMissingId) ?? missingFindings[0] ?? null
  const selectedUntracked = untrackedFindings.find((f) => f.finding_id === selectedUntrackedId) ?? untrackedFindings[0] ?? null

  const ledgerDetail = selectedLedgerEntry ?? entries.find((entry) => entry.ledger_id === selectedLedgerId) ?? null
  const affectedTables = ledgerDetail ? parseTableList(ledgerDetail.affected_tables) : []

  const tabs: { id: WorkspaceTab; label: string; icon: ReactNode; count: number | null }[] = [
    { id: 'duplicates', label: 'Duplicates', icon: <CopyCheck size={13} />, count: dupSummary?.candidates ?? null },
    { id: 'missing', label: 'Missing / Orphaned', icon: <FileX2 size={13} />, count: findings?.summary ? findings.summary.indexed_missing_file + findings.summary.stale_path : null },
    { id: 'untracked', label: 'Untracked', icon: <FileQuestion size={13} />, count: findings?.summary.untracked_file ?? null },
    { id: 'quarantine', label: 'Quarantine', icon: <Archive size={13} />, count: quarantine?.items.length ?? null },
    { id: 'plans', label: 'Plans', icon: <Workflow size={13} />, count: entries.length || null },
  ]

  return (
    <div className="page">
      <PageHeader
        title="Library Reconciliation"
        subtitle="Find, review, and plan library cleanup. Nothing here deletes, moves, or renames a file — plans stop at validation."
        actions={<button className="btn btn--ghost btn--sm" onClick={() => { void loadDuplicates(); void loadFindings(); void loadQuarantine(); void loadLedger() }}>
          <RefreshCw size={13} /> Refresh all
        </button>}
      />

      <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>
        Review workspace only: findings and plans are evidence and proposals. No automatic keeper removal, path relink, quarantine action, or plan apply exists here.
      </StatusStrip>

      <div className="reconciliation-tabs" role="tablist" aria-label="Library reconciliation sections">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className={`reconciliation-tab${tab === item.id ? ' is-active' : ''}`}
            onClick={() => setTab(item.id)}
          >
            {item.icon} {item.label}{item.count != null && <span className="badge-count">{item.count}</span>}
          </button>
        ))}
      </div>

      {tab === 'duplicates' && (
        <section className="section" role="tabpanel" aria-label="Duplicates">
          <div className="card">
            <div className="card-header"><h2 className="card-title">Duplicate review</h2></div>
            {dupError && <StatusStrip tone="danger" role="alert" onDismiss={() => setDupError(null)}>{dupError}</StatusStrip>}
            {dupLoading ? <p className="empty-state">Loading duplicate summary…</p> : (
              <div className="recon-stat-grid">
                <div className="recon-stat"><span className="recon-stat-label">Groups</span><strong>{dupSummary?.groups ?? '—'}</strong></div>
                <div className="recon-stat"><span className="recon-stat-label">Candidates</span><strong>{dupSummary?.candidates ?? '—'}</strong></div>
                <div className="recon-stat"><span className="recon-stat-label">Unresolved</span><strong>{dupSummary?.unresolved ?? '—'}</strong></div>
                <div className="recon-stat"><span className="recon-stat-label">Keep</span><strong>{dupSummary?.keep ?? '—'}</strong></div>
              </div>
            )}
            <p className="muted">{dupMessage || 'Duplicate evidence, keeper recommendations (advisory only), and review decisions live in the dedicated Duplicate Review workspace.'}</p>
            <Link className="btn btn--primary btn--sm" to="/duplicates">Open Duplicate Review</Link>
          </div>
        </section>
      )}

      {(tab === 'missing' || tab === 'untracked') && (
        <section className="section" role="tabpanel" aria-label={tab === 'missing' ? 'Missing and orphaned files' : 'Untracked files'}>
          {findingsError && <StatusStrip tone="danger" role="alert" onDismiss={() => setFindingsError(null)}>{findingsError}</StatusStrip>}
          {findings?.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}
          {findingsLoading ? <p className="empty-state">Loading findings…</p> : (
            (tab === 'missing' ? missingFindings : untrackedFindings).length === 0 ? (
              <EmptyState
                icon={<ShieldCheck size={28} />}
                title={tab === 'missing' ? 'No missing or stale-path findings' : 'No untracked files found'}
                message={findings?.message || 'The local index and the selected library are in sync for this finding type.'}
              />
            ) : (
              <div className="reconciliation-findings-layout">
                <div className="reconciliation-findings-list">
                  <div className="duplicates-panel-heading"><div><h2>{tab === 'missing' ? 'Missing / stale-path' : 'Untracked files'}</h2><p>{(tab === 'missing' ? missingFindings : untrackedFindings).length} finding(s)</p></div></div>
                  <FindingsList
                    findings={tab === 'missing' ? missingFindings : untrackedFindings}
                    selectedId={tab === 'missing' ? selectedMissing?.finding_id ?? null : selectedUntracked?.finding_id ?? null}
                    onSelect={tab === 'missing' ? setSelectedMissingId : setSelectedUntrackedId}
                  />
                </div>
                <div className="reconciliation-findings-detail">
                  <FindingDetail finding={tab === 'missing' ? selectedMissing : selectedUntracked} />
                </div>
              </div>
            )
          )}
          {tab === 'missing' && candidateCount > 0 && (
            <StatusStrip tone="info">{candidateCount} possible rename/relocation candidate(s) were found for these missing files. Propose a plan in the Plans tab to review them as specific actions.</StatusStrip>
          )}
        </section>
      )}

      {tab === 'quarantine' && (
        <section className="section" role="tabpanel" aria-label="Quarantine">
          <div className="card">
            <div className="card-header"><h2 className="card-title">Quarantine <span className="card-title-count">{quarantine?.items.length ?? 0}</span></h2></div>
            {quarantineError && <StatusStrip tone="danger" role="alert" onDismiss={() => setQuarantineError(null)}>{quarantineError}</StatusStrip>}
            {quarantine?.message && <StatusStrip tone="info">{quarantine.message}</StatusStrip>}
            {quarantineLoading ? <p className="empty-state">Loading quarantine listing…</p> : !quarantine || quarantine.items.length === 0 ? (
              <EmptyState icon={<Archive size={28} />} message="No files are currently in the quarantine directory." />
            ) : (
              <div className="reconciliation-findings-scroll">
                {quarantine.items.map((item) => (
                  <div className="reconciliation-quarantine-item" key={item.relative_path}>
                    <span><code>{item.relative_path}</code><br /><small className="muted">{formatBytes(item.size_bytes)}</small></span>
                    <Badge tone="pending">Restore not available</Badge>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {tab === 'plans' && (
        <section className="section" role="tabpanel" aria-label="Plans">
          <div className="card">
            <div className="card-header"><h2 className="card-title">Propose a plan</h2></div>
            <p className="muted">Detects candidates from the current findings and writes a plan artifact for review. Nothing is applied — apply is intentionally not built in this workspace.</p>
            {proposeError && <StatusStrip tone="danger" role="alert" onDismiss={() => setProposeError(null)}>{proposeError}</StatusStrip>}
            <div className="reconciliation-plan-actions">
              <button className="btn btn--primary btn--sm" onClick={() => void proposePlan()} disabled={proposing}>
                {proposing ? <Loader2 size={13} className="spin" /> : <Workflow size={13} />} {proposing ? 'Proposing…' : 'Propose plan'}
              </button>
              <button className="btn btn--ghost btn--sm" onClick={() => void validateLatestPlan()} disabled={loadingValidation}>
                {loadingValidation ? <Loader2 size={13} className="spin" /> : <ShieldCheck size={13} />} Validate latest plan
              </button>
            </div>
            {proposal && (
              <div className="reconciliation-plan-summary">
                <p>Plan artifact: <code>{proposal.plan_artifact}</code> · apply supported: <strong>{String(proposal.apply_supported)}</strong></p>
                <div className="table-wrapper">
                  <table className="table recon-table">
                    <thead><tr><th>Action</th><th>Old path</th><th>New path</th><th>Risk</th><th>Review tier</th></tr></thead>
                    <tbody>
                      {proposal.planned_actions.length === 0 ? <tr><td colSpan={5} className="muted">No actions proposed from current findings.</td></tr> : proposal.planned_actions.map((action, index) => (
                        // Plan actions have no stable id from the API; index is fine since this list re-renders wholesale per proposal.
                        <tr key={index}>
                          <td className="td-mono">{String(action.action ?? '—')}</td>
                          <td className="td-mono recon-path">{String(action.old_path ?? '—')}</td>
                          <td className="td-mono recon-path">{String(action.new_path ?? '—')}</td>
                          <td>{String(action.risk ?? '—')}</td>
                          <td>{String(action.review_tier ?? '—')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-header"><h2 className="card-title">Plan validation</h2></div>
            {validationError && <StatusStrip tone="danger" role="alert" onDismiss={() => setValidationError(null)}>{validationError}</StatusStrip>}
            {validation ? (
              <div className="recon-validation">
                <div className="recon-stat-grid">
                  <div className="recon-stat"><span className="recon-stat-label">Total</span><strong>{validation.total_actions}</strong></div>
                  <div className="recon-stat"><span className="recon-stat-label">Valid</span><strong>{validation.valid_actions}</strong></div>
                  <div className="recon-stat"><span className="recon-stat-label">Invalid</span><strong>{validation.invalid_actions}</strong></div>
                  <div className="recon-stat"><span className="recon-stat-label">Skipped</span><strong>{validation.skipped_actions}</strong></div>
                </div>
                <div className="recon-validation-meta">
                  <span className="muted">Generated: {formatDateTime(validation.generated_at)}</span>
                </div>
                <div className="recon-validation-grid">
                  <div className="table-wrapper">
                    <table className="table recon-table">
                      <thead><tr><th>Reason</th><th style={{ width: 120 }}>Count</th></tr></thead>
                      <tbody>
                        {Object.entries(validation.reasons).length > 0 ? Object.entries(validation.reasons).map(([reason, count]) => (
                          <tr key={reason}><td className="td-mono">{reason}</td><td>{count}</td></tr>
                        )) : <tr><td colSpan={2} className="muted">No invalid reasons reported.</td></tr>}
                      </tbody>
                    </table>
                  </div>
                  <div className="table-wrapper">
                    <table className="table recon-table">
                      <thead><tr><th>Action</th><th>Status</th><th>Reason</th></tr></thead>
                      <tbody>
                        {validation.validation_records.filter((record) => record.status !== 'valid').length > 0 ? (
                          validation.validation_records.filter((record) => record.status !== 'valid').map((record) => (
                            <tr key={`${record.action_type}:${record.reason ?? ''}:${JSON.stringify(record.action)}`}>
                              <td className="td-mono">{record.action_type}</td>
                              <td><LedgerBadge status={record.status} /></td>
                              <td className="td-mono">{record.reason || '—'}</td>
                            </tr>
                          ))
                        ) : <tr><td colSpan={3} className="muted">No invalid actions.</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            ) : <EmptyState message="Propose or validate a plan to inspect its actions." />}
          </div>

          <div className="card">
            <div className="card-header"><h2 className="card-title">Ledger table <span className="card-title-count">{entries.length}</span></h2></div>
            {ledgerError && <StatusStrip tone="danger" role="alert" onDismiss={() => setLedgerError(null)}>{ledgerError}</StatusStrip>}
            {loadingLedgerList ? <p className="empty-state">Loading ledger entries…</p> : entries.length === 0 ? <EmptyState message="No ledger entries found." /> : (
              <div className="recon-grid">
                <div className="table-wrapper">
                  <table className="table recon-table">
                    <thead><tr><th>Ledger ID</th><th className="nowrap">Timestamp</th><th>Operation</th><th>Status</th></tr></thead>
                    <tbody>
                      {entries.map((entry) => (
                        <tr key={entry.ledger_id} className={`row--clickable${entry.ledger_id === selectedLedgerId ? ' row--selected' : ''}`} onClick={() => setSelectedLedgerId(entry.ledger_id)}>
                          <td className="td-mono">{entry.ledger_id}</td>
                          <td className="nowrap">{formatDateTime(entry.created_at)}</td>
                          <td className="td-mono">{entry.operation_type || '—'}</td>
                          <td><LedgerBadge status={entry.status} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="recon-detail">
                  {loadingLedgerDetail ? <p className="empty-state">Loading entry…</p> : ledgerDetail ? (
                    <>
                      <dl className="def-list recon-detail-fields">
                        <DetailField label="Ledger ID" value={ledgerDetail.ledger_id} />
                        <DetailField label="Timestamp" value={formatDateTime(ledgerDetail.created_at)} />
                        <DetailField label="Operation type" value={ledgerDetail.operation_type || '—'} />
                        <DetailField label="Status" value={<LedgerBadge status={ledgerDetail.status} />} />
                        <DetailField label="Old path" value={ledgerDetail.old_path || '—'} />
                        <DetailField label="New path" value={ledgerDetail.new_path || '—'} />
                        <DetailField label="Affected tables" value={affectedTables.join(', ') || '—'} />
                        <DetailField label="Error" value={ledgerDetail.error || '—'} />
                      </dl>
                      <div className="recon-json-block"><div className="recon-json-label">Before values</div><pre className="recon-json">{prettyJson(ledgerDetail.before_values_json)}</pre></div>
                      <div className="recon-json-block"><div className="recon-json-label">After values</div><pre className="recon-json">{prettyJson(ledgerDetail.after_values_json)}</pre></div>
                    </>
                  ) : <EmptyState message="Select a ledger entry to inspect it." />}
                </div>
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
