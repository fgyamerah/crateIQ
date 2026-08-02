import type { ReactNode } from 'react'
import { useEffect, useMemo, useState } from 'react'
import { Loader2, RefreshCw, ShieldCheck } from 'lucide-react'
import { ApiError } from '../api/client'
import {
  fetchReconciliationLedger,
  fetchReconciliationLedgerEntry,
  validateReconciliationPlan,
} from '../api/reconciliation'
import type { ReconciliationLedgerEntry } from '../types/reconciliation'
import type { ReconciliationPlanValidationResult } from '../types/reconciliation'
import PageHeader from '../components/PageHeader'
import Badge, { type BadgeTone } from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import StatusStrip from '../components/ui/StatusStrip'

function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

function parseTableList(value: string | null): string[] {
  if (!value) return []
  try {
    const parsed = JSON.parse(value)
    if (Array.isArray(parsed)) {
      return parsed.map((item) => String(item)).filter((item) => item.trim())
    }
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
  if (value.includes('fail') || value.includes('error')) return 'failed'
  if (value.includes('pend') || value.includes('queue')) return 'pending'
  if (value.includes('ok') || value.includes('success') || value.includes('applied') || value.includes('done')) {
    return 'succeeded'
  }
  return 'info'
}

function LedgerBadge({ status }: { status: string | null }) {
  return <Badge tone={statusTone(status)}>{status || 'unknown'}</Badge>
}

function DetailField({ label, value }: { label: string; value: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  )
}

export default function Reconciliation() {
  const [entries, setEntries] = useState<ReconciliationLedgerEntry[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedEntry, setSelectedEntry] = useState<ReconciliationLedgerEntry | null>(null)
  const [validation, setValidation] = useState<ReconciliationPlanValidationResult | null>(null)
  const [loadingList, setLoadingList] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [loadingValidation, setLoadingValidation] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)

  const selectedSummary = useMemo(
    () => entries.find((entry) => entry.ledger_id === selectedId) ?? null,
    [entries, selectedId],
  )

  async function loadLedger() {
    setLoadingList(true)
    setError(null)
    try {
      const rows = await fetchReconciliationLedger()
      setEntries(rows)
      setSelectedId((current) => {
        if (current && rows.some((entry) => entry.ledger_id === current)) return current
        return rows[0]?.ledger_id ?? null
      })
      if (!rows.length) {
        setSelectedEntry(null)
      }
    } catch (err) {
      const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : String(err)
      setError(msg)
    } finally {
      setLoadingList(false)
    }
  }

  async function validateLatestPlan() {
    setLoadingValidation(true)
    setValidationError(null)
    try {
      const result = await validateReconciliationPlan({ latest: true })
      setValidation(result)
    } catch (err) {
      const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : String(err)
      setValidationError(msg)
    } finally {
      setLoadingValidation(false)
    }
  }

  useEffect(() => {
    void loadLedger()
  }, [])

  useEffect(() => {
    if (!selectedId) return
    let cancelled = false
    setLoadingDetail(true)
    setError(null)
    void fetchReconciliationLedgerEntry(selectedId)
      .then((entry) => {
        if (!cancelled) {
          setSelectedEntry(entry)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : String(err)
          setError(msg)
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingDetail(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  useEffect(() => {
    if (!selectedId && entries.length > 0) {
      setSelectedId(entries[0].ledger_id)
    }
  }, [entries, selectedId])

  const tableRows = entries.length
  const detail = selectedEntry ?? selectedSummary
  const affectedTables = detail ? parseTableList(detail.affected_tables) : []

  return (
    <div className="page">
      <PageHeader
        title="Reconciliation Ledger"
        subtitle="Read-only ledger records for path reconciliation work."
        actions={(
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn--ghost btn--sm" onClick={() => void validateLatestPlan()} disabled={loadingValidation}>
              {loadingValidation ? <Loader2 size={13} className="spin" /> : <ShieldCheck size={13} />}
              Validate Latest Plan
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => void loadLedger()} disabled={loadingList}>
              {loadingList ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}
              Refresh
            </button>
          </div>
        )}
      />

      {error && (
        <StatusStrip tone="danger" role="alert" onDismiss={() => setError(null)}>
          <strong>Error:</strong> {error}
        </StatusStrip>
      )}
      {validationError && (
        <StatusStrip tone="danger" role="alert" onDismiss={() => setValidationError(null)}>
          <strong>Error:</strong> {validationError}
        </StatusStrip>
      )}

      <section className="section">
        <div className="recon-grid">
          <div className="card recon-list-card">
            <div className="card-header">
              <h2 className="card-title">Ledger table <span className="card-title-count">{tableRows}</span></h2>
            </div>
            {loadingList ? (
              <p className="empty-state">Loading ledger entries…</p>
            ) : tableRows === 0 ? (
              <EmptyState message="No ledger entries found." />
            ) : (
              <div className="table-wrapper">
                <table className="table recon-table">
                  <thead>
                    <tr>
                      <th>Ledger ID</th>
                      <th className="nowrap">Timestamp</th>
                      <th>Operation</th>
                      <th>Status</th>
                      <th>Root</th>
                      <th>Affected tables</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map((entry) => {
                      const selected = entry.ledger_id === selectedId
                      return (
                        <tr
                          key={entry.ledger_id}
                          className={`row--clickable${selected ? ' row--selected' : ''}`}
                          onClick={() => setSelectedId(entry.ledger_id)}
                        >
                          <td className="td-mono">{entry.ledger_id}</td>
                          <td className="nowrap">{formatDateTime(entry.created_at)}</td>
                          <td className="td-mono">{entry.operation_type || '—'}</td>
                          <td><LedgerBadge status={entry.status} /></td>
                          <td className="td-mono recon-path">{entry.root || '—'}</td>
                          <td className="recon-tables">{parseTableList(entry.affected_tables).join(', ') || '—'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card recon-detail-card">
            <div className="card-header">
              <h2 className="card-title">Ledger detail inspector</h2>
            </div>
            {loadingDetail ? (
              <p className="empty-state">Loading entry…</p>
            ) : detail ? (
              <div className="recon-detail">
                <dl className="def-list recon-detail-fields">
                  <DetailField label="Ledger ID" value={detail.ledger_id} />
                  <DetailField label="Timestamp" value={formatDateTime(detail.created_at)} />
                  <DetailField label="Operation type" value={detail.operation_type || '—'} />
                  <DetailField label="Status" value={<LedgerBadge status={detail.status} />} />
                  <DetailField label="Root" value={detail.root || '—'} />
                  <DetailField label="Old path" value={detail.old_path || '—'} />
                  <DetailField label="New path" value={detail.new_path || '—'} />
                  <DetailField label="Affected tables" value={affectedTables.join(', ') || '—'} />
                  <DetailField label="Error" value={detail.error || '—'} />
                </dl>
                <div className="recon-json-block">
                  <div className="recon-json-label">Before values</div>
                  <pre className="recon-json">{prettyJson(detail.before_values_json)}</pre>
                </div>
                <div className="recon-json-block">
                  <div className="recon-json-label">After values</div>
                  <pre className="recon-json">{prettyJson(detail.after_values_json)}</pre>
                </div>
              </div>
            ) : (
              <EmptyState message="Select a ledger entry to inspect it." />
            )}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Plan validation</h2>
          </div>
          {validation ? (
            <div className="recon-validation">
              <div className="recon-stat-grid">
                <div className="recon-stat">
                  <span className="recon-stat-label">Total</span>
                  <strong>{validation.total_actions}</strong>
                </div>
                <div className="recon-stat">
                  <span className="recon-stat-label">Valid</span>
                  <strong>{validation.valid_actions}</strong>
                </div>
                <div className="recon-stat">
                  <span className="recon-stat-label">Invalid</span>
                  <strong>{validation.invalid_actions}</strong>
                </div>
                <div className="recon-stat">
                  <span className="recon-stat-label">Skipped</span>
                  <strong>{validation.skipped_actions}</strong>
                </div>
              </div>

              <div className="recon-validation-meta">
                <span className="muted">Plan: <code>{validation.plan_path}</code></span>
                <span className="muted">Generated: {formatDateTime(validation.generated_at)}</span>
              </div>

              <div className="recon-validation-grid">
                <div className="table-wrapper">
                  <table className="table recon-table">
                    <thead>
                      <tr>
                        <th>Reason</th>
                        <th style={{ width: 120 }}>Count</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(validation.reasons).length > 0 ? (
                        Object.entries(validation.reasons).map(([reason, count]) => (
                          <tr key={reason}>
                            <td className="td-mono">{reason}</td>
                            <td>{count}</td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={2} className="muted">No invalid reasons reported.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>

                <div className="table-wrapper">
                  <table className="table recon-table">
                    <thead>
                      <tr>
                        <th>Action</th>
                        <th>Status</th>
                        <th>Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {validation.validation_records.filter((record) => record.status !== 'valid').length > 0 ? (
                        validation.validation_records
                          .filter((record) => record.status !== 'valid')
                          .map((record) => (
                            // No real ID on validation records; key on the
                            // stable plan-action content instead of the
                            // (unstable) filtered-array index.
                            <tr key={`${record.action_type}:${record.reason ?? ''}:${JSON.stringify(record.action)}`}>
                              <td className="td-mono">{record.action_type}</td>
                              <td><LedgerBadge status={record.status} /></td>
                              <td className="td-mono">{record.reason || '—'}</td>
                            </tr>
                          ))
                      ) : (
                        <tr>
                          <td colSpan={3} className="muted">No invalid actions.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          ) : (
            <EmptyState message="Run validation to inspect the latest reconcile plan." />
          )}
        </div>
      </section>
    </div>
  )
}
