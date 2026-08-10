import type { ReactNode } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Archive, CopyCheck, FileQuestion, FileX2, Loader2, RefreshCw, ShieldCheck, Workflow } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchDuplicateReview } from '../api/duplicates'
import {
  fetchReconciliationFindings,
  fetchReconciliationLedger,
  fetchReconciliationLedgerEntry,
  fetchReconciliationQuarantine,
  previewReconciliationApply,
  applyReconciliationPlan,
  rollbackReconciliationLedger,
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
  ReconciliationApplyPreviewResponse,
  ReconciliationApplyResponse,
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

function ledgerJson(value: string | null): Record<string, unknown> | null {
  if (!value) return null
  try {
    const parsed: unknown = JSON.parse(value)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null
  } catch {
    return null
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

type ActionBoundPreview = {
  actionId: string
  requestId: number
  data: ReconciliationApplyPreviewResponse
}

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
  const [selectedActionId, setSelectedActionId] = useState<string | null>(null)
  const [applyPreview, setApplyPreview] = useState<ActionBoundPreview | null>(null)
  const [applyResult, setApplyResult] = useState<ReconciliationApplyResponse | null>(null)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [applyConfirm, setApplyConfirm] = useState(false)
  const [applying, setApplying] = useState(false)
  const [rollbackConfirmation, setRollbackConfirmation] = useState<{ ledgerId: string; confirmed: boolean } | null>(null)
  const [rollingBack, setRollingBack] = useState(false)
  const previewRequestId = useRef(0)
  const ledgerDetailRequestId = useRef(0)
  const selectedLedgerIdRef = useRef<string | null>(null)

  function selectLedger(ledgerId: string | null) {
    // Selection is the authorization boundary: never retain a detail,
    // confirmation, or error that describes a different ledger row.
    ledgerDetailRequestId.current += 1
    selectedLedgerIdRef.current = ledgerId
    setSelectedLedgerId(ledgerId)
    setSelectedLedgerEntry(null)
    setRollbackConfirmation(null)
    setLedgerError(null)
    setLoadingLedgerDetail(Boolean(ledgerId))
  }

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

  async function loadLedger(): Promise<ReconciliationLedgerEntry[]> {
    setLoadingLedgerList(true)
    setLedgerError(null)
    try {
      const rows = await fetchReconciliationLedger()
      setEntries(rows)
      const current = selectedLedgerIdRef.current
      const next = (current && rows.some((entry) => entry.ledger_id === current)) ? current : rows[0]?.ledger_id ?? null
      if (next !== current) selectLedger(next)
      if (!next) setSelectedLedgerEntry(null)
      return rows
    } catch (err) {
      setLedgerError(messageFor(err, 'Could not load the reconciliation ledger.'))
    } finally {
      setLoadingLedgerList(false)
    }
    return []
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
      previewRequestId.current += 1
      setApplyPreview(null)
      setApplyResult(null)
      setSelectedActionId(null)
    } catch (err) {
      setValidationError(messageFor(err, 'Could not validate the latest plan.'))
    } finally {
      setLoadingValidation(false)
    }
  }

  async function previewSelectedAction(actionId: string) {
    if (!validation?.plan_path) return
    const requestId = ++previewRequestId.current
    setSelectedActionId(actionId)
    setApplyPreview(null)
    setApplyResult(null)
    setApplyError(null)
    setApplyConfirm(false)
    try {
      const data = await previewReconciliationApply({ plan_path: validation.plan_path, reviewed_action_ids: [actionId] })
      // Do not let a late request replace the authorization data for the
      // currently selected action. Request identity is the correctness
      // boundary even if transport cancellation is added later.
      if (previewRequestId.current === requestId) setApplyPreview({ actionId, requestId, data })
    } catch (err) {
      if (previewRequestId.current === requestId) setApplyError(messageFor(err, 'Could not check DB-only apply eligibility.'))
    }
  }

  async function refreshApplyAuthorization() {
    if (!validation?.plan_path) return
    previewRequestId.current += 1
    setApplyPreview(null)
    setSelectedActionId(null)
    setApplyConfirm(false)
    try {
      setValidation(await validateReconciliationPlan({ plan_path: validation.plan_path }))
    } catch (err) {
      setValidationError(messageFor(err, 'Could not refresh reconciliation plan validation.'))
    }
  }

  async function applySelectedAction() {
    const currentPreview = applyPreview?.actionId === selectedActionId ? applyPreview.data : null
    const eligibility = currentPreview?.actions[0]
    if (!validation?.plan_path || !selectedActionId || !currentPreview || !eligibility?.eligible || !applyConfirm) return
    setApplying(true)
    setApplyError(null)
    try {
      const result = await applyReconciliationPlan({ plan_path: validation.plan_path, plan_id: currentPreview.plan_id, reviewed_action_ids: [selectedActionId], confirm: true })
      setApplyResult(result)
      await loadLedger()
      await refreshApplyAuthorization()
    } catch (err) {
      setApplyError(messageFor(err, 'Could not apply the reviewed DB-only action.'))
    } finally {
      setApplying(false)
    }
  }

  async function rollbackSelectedLedger() {
    const ledgerId = selectedLedgerId
    const ledgerDetail = selectedLedgerEntry?.ledger_id === ledgerId ? selectedLedgerEntry : null
    const confirmationMatches = rollbackConfirmation?.ledgerId === ledgerId && rollbackConfirmation.confirmed
    if (!ledgerId || !ledgerDetail || !canRollbackLedger || !confirmationMatches) return
    setRollingBack(true)
    setLedgerError(null)
    try {
      const result = await rollbackReconciliationLedger(ledgerId, true)
      setRollbackConfirmation(null)
      await loadLedger()
      selectLedger(result.ledger_id)
      await refreshApplyAuthorization()
    } catch (err) {
      setLedgerError(messageFor(err, 'Could not rollback this reconciliation entry.'))
    } finally {
      setRollingBack(false)
    }
  }

  useEffect(() => { void loadDuplicates() }, [])
  useEffect(() => { void loadFindings() }, [])
  useEffect(() => { void loadQuarantine() }, [])
  useEffect(() => { void loadLedger() }, [])

  useEffect(() => {
    const requestedLedgerId = selectedLedgerId
    if (!requestedLedgerId) {
      setSelectedLedgerEntry(null)
      setLoadingLedgerDetail(false)
      return
    }
    const requestId = ++ledgerDetailRequestId.current
    setSelectedLedgerEntry(null)
    setRollbackConfirmation(null)
    setLoadingLedgerDetail(true)
    void fetchReconciliationLedgerEntry(requestedLedgerId)
      .then((entry) => {
        if (ledgerDetailRequestId.current === requestId && selectedLedgerIdRef.current === requestedLedgerId && entry.ledger_id === requestedLedgerId) {
          setSelectedLedgerEntry(entry)
        }
      })
      .catch((err) => {
        if (ledgerDetailRequestId.current === requestId && selectedLedgerIdRef.current === requestedLedgerId) {
          setLedgerError(messageFor(err, 'Could not load ledger entry.'))
        }
      })
      .finally(() => {
        if (ledgerDetailRequestId.current === requestId && selectedLedgerIdRef.current === requestedLedgerId) setLoadingLedgerDetail(false)
      })
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

  const ledgerDetail = selectedLedgerEntry?.ledger_id === selectedLedgerId ? selectedLedgerEntry : null
  const affectedTables = ledgerDetail ? parseTableList(ledgerDetail.affected_tables) : []
  const currentPreview = applyPreview?.actionId === selectedActionId ? applyPreview.data : null
  const currentEligibility = currentPreview?.actions[0]
  const ledgerBefore = ledgerDetail ? ledgerJson(ledgerDetail.before_values_json) : null
  const supportedRollbackOperation = ledgerDetail?.operation_type === 'update_path_reference' || ledgerDetail?.operation_type === 'mark_stale_processed_state_path'
  const currentLedgerHasRollback = ledgerDetail ? entries.some((entry) => {
    if (!entry.operation_type?.startsWith('rollback:')) return false
    return ledgerJson(entry.before_values_json)?.rollback_of_ledger_id === ledgerDetail.ledger_id
  }) : false
  const canRollbackLedger = Boolean(
    ledgerDetail?.status === 'applied'
    && supportedRollbackOperation
    && ledgerBefore?.apply_contract === 'crateiq_reconciliation_db_only_v1'
    && ledgerBefore?.verification_status === 'verified'
    && !currentLedgerHasRollback,
  )
  const rollbackConfirmedForCurrentLedger = rollbackConfirmation?.ledgerId === selectedLedgerId && rollbackConfirmation.confirmed

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
        subtitle="Find, review, validate, and narrowly update database references. Music files are never moved, renamed, or deleted here."
        actions={<button className="btn btn--ghost btn--sm" onClick={() => { void loadDuplicates(); void loadFindings(); void loadQuarantine(); void loadLedger() }}>
          <RefreshCw size={13} /> Refresh all
        </button>}
      />

      <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>
        DB-only reconciliation: a reviewed, validated action can update CrateIQ database path references with a verified backup. Music files are not moved.
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
            <p className="muted">Detects candidates from the current findings and writes a plan artifact for review. Reviewed DB-only apply is available for supported validated path-reference actions; music files are never moved, renamed, deleted, or tag-written.</p>
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
                <p>Plan artifact: <code>{proposal.plan_artifact}</code> · validate it before a reviewed DB-only action can be selected.</p>
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
                <div className="reconciliation-apply-panel">
                  <h3>Reviewed DB-only apply</h3>
                  <p className="muted">Select exactly one validated action. This updates CrateIQ database path references only, creates and verifies a SQLite-consistent database backup first, and never moves, renames, deletes, or tag-writes music files.</p>
                  <div className="table-wrapper">
                    <table className="table recon-table">
                      <thead><tr><th>Action</th><th>DB reference change</th><th>Eligibility</th><th>Review</th></tr></thead>
                      <tbody>
                        {validation.validation_records.map((record) => {
                          const action = (record.action && typeof record.action === 'object' ? record.action : {}) as Record<string, unknown>
                          const supported = record.action_type === 'update_path_reference' || record.action_type === 'mark_stale_processed_state_path'
                          return <tr key={record.action_id ?? `${record.action_type}:${JSON.stringify(action)}`}>
                            <td className="td-mono">{record.action_type}</td>
                            <td className="td-mono recon-path">{String(action.old_path ?? '—')} → {String(action.new_path ?? action.replacement_path ?? '—')}</td>
                            <td><LedgerBadge status={record.status} /></td>
                            <td><button className="btn btn--ghost btn--sm" disabled={!supported || record.status === 'invalid' || !record.action_id} onClick={() => record.action_id && void previewSelectedAction(record.action_id)}>Review DB-only change</button></td>
                          </tr>
                        })}
                      </tbody>
                    </table>
                  </div>
                  {applyError && <StatusStrip tone="danger" role="alert" onDismiss={() => setApplyError(null)}>{applyError}</StatusStrip>}
                  {currentPreview && currentEligibility && <div className="reconciliation-apply-result">
                    <p><strong>Action ID:</strong> <code>{currentEligibility.action_id}</code> · <strong>Operation:</strong> <code>{currentEligibility.operation_type}</code> · <strong>{currentEligibility.eligible ? 'eligible' : 'blocked'}</strong></p>
                    <dl className="def-list recon-detail-fields">
                      <DetailField label="Current DB reference (before)" value={<code>{String(currentEligibility.action?.old_path ?? '—')}</code>} />
                      <DetailField label="Proposed DB reference (after)" value={<code>{String(currentEligibility.action?.new_path ?? currentEligibility.action?.replacement_path ?? '—')}</code>} />
                      <DetailField label="Scope" value="DB-only; no music-file, tag, BPM, key, or cue change" />
                    </dl>
                    {currentEligibility.blockers.length ? <StatusStrip tone="warn">Blockers: {currentEligibility.blockers.join(', ')}</StatusStrip> : <>
                      <label className="reconciliation-confirm"><input type="checkbox" checked={applyConfirm} onChange={(event) => setApplyConfirm(event.target.checked)} /> I authorize action <code>{currentEligibility.action_id}</code> to change exactly the displayed DB reference. I understand music files are not moved.</label>
                      <button className="btn btn--primary btn--sm" disabled={!applyConfirm || applying || currentPreview.actions.length !== 1 || !currentEligibility.eligible} onClick={() => void applySelectedAction()}>{applying ? 'Applying DB reference…' : 'Apply reviewed DB-only change'}</button>
                    </>}
                  </div>}
                  {applyResult && <StatusStrip tone="good">{applyResult.message} Action: {applyResult.results.map((item) => item.action_id).join(', ')}. Verification: {applyResult.results.map((item) => item.verification_status).join(', ')}.</StatusStrip>}
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
                        <tr key={entry.ledger_id} className={`row--clickable${entry.ledger_id === selectedLedgerId ? ' row--selected' : ''}`} onClick={() => selectLedger(entry.ledger_id)}>
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
                      {canRollbackLedger && <div className="reconciliation-rollback">
                        <label className="reconciliation-confirm"><input type="checkbox" checked={rollbackConfirmedForCurrentLedger} onChange={(event) => setRollbackConfirmation({ ledgerId: ledgerDetail.ledger_id, confirmed: event.target.checked })} /> I confirm rollback of this exact verified DB-only ledger entry.</label>
                        <button className="btn btn--ghost btn--sm" disabled={!rollbackConfirmedForCurrentLedger || rollingBack || ledgerDetail.ledger_id !== selectedLedgerId} onClick={() => void rollbackSelectedLedger()}>{rollingBack ? 'Rolling back…' : 'Rollback DB-only action'}</button>
                      </div>}
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
