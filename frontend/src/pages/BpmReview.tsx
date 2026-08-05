import { useState, useEffect, useCallback } from 'react'
import { RefreshCw, Loader2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  runBpmCheck,
  fetchBpmAnomalies,
  fetchBpmSummary,
  updateAnomaly,
} from '../api/analysis'
import ErrorBanner from '../components/ErrorBanner'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/ui/EmptyState'
import StatusStrip from '../components/ui/StatusStrip'
import type { BpmAnomaly, AnomalyReviewStatus, BpmSummary } from '../types/analysis'
import { REASON_COLORS, STATUS_COLORS } from '../types/analysis'
import { ApiError } from '../api/client'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtBpm(v: number | null): string {
  if (v == null) return '—'
  return v.toFixed(1)
}

function trackLabel(a: BpmAnomaly): string {
  if (a.artist && a.title) return `${a.artist} — ${a.title}`
  if (a.title) return a.title
  return a.filepath.split('/').pop() ?? a.filepath
}

const REASON_DESCRIPTIONS: Record<string, string> = {
  missing_bpm:    'No BPM stored — track was never analysed or value was missing.',
  too_low_10x:    'BPM < 20 — likely a ×10 scale error (e.g. 12.1 stored instead of 121).',
  likely_halved:  'BPM 20–90 — aubio commonly halves tempo for complex tracks.',
  likely_doubled: 'BPM 160–240 — aubio commonly doubles tempo for certain genres.',
  too_high:       'BPM > 240 — almost certainly wrong for any standard genre.',
}

// ---------------------------------------------------------------------------
// SummaryBar
// ---------------------------------------------------------------------------

function SummaryBar({ summary }: { summary: BpmSummary | null }) {
  if (!summary) return null
  const { by_status, by_reason } = summary
  const total = Object.values(by_status).reduce((a, b) => a + b, 0)
  if (total === 0) return <EmptyState message="No anomalies recorded. Run a BPM check first." />
  return (
    <div className="bpm-summary-bar">
      <div className="bpm-summary-section">
        <span className="bpm-summary-label">By status</span>
        {Object.entries(by_status).map(([k, v]) => (
          <span key={k} className={`anomaly-status-pill ${STATUS_COLORS[k as AnomalyReviewStatus] ?? ''}`}>
            {k} <strong>{v}</strong>
          </span>
        ))}
      </div>
      <div className="bpm-summary-section">
        <span className="bpm-summary-label">By reason</span>
        {Object.entries(by_reason).map(([k, v]) => (
          <span key={k} className="bpm-reason-pill">{k} <strong>{v}</strong></span>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AnomalyRow
// ---------------------------------------------------------------------------

interface AnomalyRowProps {
  anomaly: BpmAnomaly
  onUpdate: (updated: BpmAnomaly) => void
}

function AnomalyRow({ anomaly: a, onUpdate }: AnomalyRowProps) {
  const [busy, setBusy] = useState(false)

  async function setStatus(status: AnomalyReviewStatus, note?: string) {
    setBusy(true)
    try {
      const updated = await updateAnomaly(a.id, { review_status: status, review_note: note })
      onUpdate(updated)
    } catch {
      // Surface silently — the row stays unchanged
    } finally {
      setBusy(false)
    }
  }

  const isPending   = a.review_status === 'pending'
  const isIgnored   = a.review_status === 'ignored'
  const isReviewed  = a.review_status === 'reviewed'
  const isRequeued  = a.review_status === 'requeued'

  return (
    <tr className={`anomaly-row anomaly-row--${a.review_status}`}>
      {/* Track */}
      <td className="td-track">
        <div className="td-track-name">{trackLabel(a)}</div>
        {a.genre && <div className="td-track-genre muted">{a.genre}</div>}
      </td>

      {/* Current BPM */}
      <td className="td-bpm-current">
        <span className={a.current_bpm == null ? 'muted' : ''}>
          {fmtBpm(a.current_bpm)}
        </span>
      </td>

      {/* Suggested BPM */}
      <td className="td-bpm-suggested">
        {a.suggested_bpm != null ? (
          <span className="bpm-suggestion">{fmtBpm(a.suggested_bpm)}</span>
        ) : <span className="muted">—</span>}
      </td>

      {/* Reason */}
      <td>
        <span
          className={`reason-badge ${REASON_COLORS[a.reason] ?? ''}`}
          title={REASON_DESCRIPTIONS[a.reason] ?? a.reason}
        >
          {a.reason_label}
        </span>
      </td>

      {/* Review status */}
      <td>
        <span className={`anomaly-status-badge ${STATUS_COLORS[a.review_status] ?? ''}`}>
          {a.review_status}
        </span>
      </td>

      {/* Actions */}
      <td className="td-actions">
        {isPending && (
          <>
            <button
              className="btn btn--ghost btn--xs"
              disabled={busy}
              onClick={() => setStatus('reviewed')}
              title="Mark as reviewed (BPM accepted as-is)"
            >
              Reviewed
            </button>
            <button
              className="btn btn--ghost btn--xs"
              disabled={busy}
              onClick={() => setStatus('requeued')}
              title="Mark as queued for re-analysis"
            >
              Queue
            </button>
            <button
              className="btn btn--ghost btn--xs btn--muted"
              disabled={busy}
              onClick={() => setStatus('ignored')}
              title="Ignore this flag"
            >
              Ignore
            </button>
          </>
        )}
        {(isReviewed || isIgnored || isRequeued) && (
          <button
            className="btn btn--ghost btn--xs btn--muted"
            disabled={busy}
            onClick={() => setStatus('pending')}
            title="Reset to pending"
          >
            Reset
          </button>
        )}
      </td>
    </tr>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type FilterStatus = 'pending' | 'all' | 'reviewed' | 'ignored' | 'requeued'
type FilterReason = '' | 'missing_bpm' | 'too_low_10x' | 'likely_halved' | 'likely_doubled' | 'too_high'

export default function BpmReview() {
  const [anomalies, setAnomalies] = useState<BpmAnomaly[]>([])
  const [summary, setSummary]     = useState<BpmSummary | null>(null)
  const [loading, setLoading]     = useState(false)
  const [scanning, setScanning]   = useState(false)
  const [error, setError]         = useState<string | null>(null)
  const [scanResult, setScanResult] = useState<string | null>(null)

  const [filterStatus, setFilterStatus] = useState<FilterStatus>('pending')
  const [filterReason, setFilterReason] = useState<FilterReason>('')

  // ---------------------------------------------------------------------------
  // Load stored anomalies
  // ---------------------------------------------------------------------------

  const loadAnomalies = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [data, sum] = await Promise.all([
        fetchBpmAnomalies({
          status: filterStatus === 'all' ? undefined : filterStatus,
          reason: filterReason || undefined,
          limit: 500,
        }),
        fetchBpmSummary(),
      ])
      setAnomalies(data)
      setSummary(sum)
    } catch (e) {
      setError(e instanceof ApiError ? e.displayMessage : e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [filterStatus, filterReason])

  useEffect(() => { loadAnomalies() }, [loadAnomalies])

  // ---------------------------------------------------------------------------
  // Run BPM check scan
  // ---------------------------------------------------------------------------

  async function handleBpmCheck() {
    setScanning(true)
    setScanResult(null)
    setError(null)
    try {
      const result = await runBpmCheck()
      setScanResult(
        `Scanned ${result.tracks_scanned} tracks — ` +
        `${result.total_active} anomalies active` +
        (result.new_anomalies > 0 ? `, ${result.new_anomalies} new` : '') +
        (result.resolved > 0 ? `, ${result.resolved} resolved` : ''),
      )
      // Refresh list and summary after scan
      const [data, sum] = await Promise.all([
        fetchBpmAnomalies({
          status: filterStatus === 'all' ? undefined : filterStatus,
          reason: filterReason || undefined,
          limit: 500,
        }),
        fetchBpmSummary(),
      ])
      setAnomalies(data)
      setSummary(sum)
    } catch (e) {
      setError(e instanceof ApiError ? e.displayMessage : e instanceof Error ? e.message : 'Scan failed')
    } finally {
      setScanning(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Row update callback
  // ---------------------------------------------------------------------------

  function handleUpdate(updated: BpmAnomaly) {
    setAnomalies(prev => prev.map(a => a.id === updated.id ? updated : a))
  }

  const pendingCount  = anomalies.filter(a => a.review_status === 'pending').length
  const requeuedCount = anomalies.filter(a => a.review_status === 'requeued').length

  return (
    <div className="page">
      <PageHeader
        title="BPM Review"
        subtitle="Review stored BPM anomalies without starting audio analysis."
        actions={
          <>
            {loading && <span className="muted" style={{ fontSize: 12 }}>Loading…</span>}
            <button
              className="btn btn--primary btn--sm"
              onClick={handleBpmCheck}
              disabled={scanning}
              title="Review stored BPM values in the local CrateIQ index"
            >
              {scanning ? <><Loader2 size={13} className="spin" /> Checking…</> : 'Review BPM anomalies'}
            </button>
            <button className="btn btn--ghost btn--sm" onClick={loadAnomalies}>
              <RefreshCw size={13} />
              Refresh
            </button>
          </>
        }
      />

      <ErrorBanner message={error} />

      {scanResult && (
        <div className="scan-result-banner">{scanResult}</div>
      )}

      {/* Summary */}
      <section className="section">
        <SummaryBar summary={summary} />
      </section>

      {/* Optional analysis guidance */}
      <section className="section">
        <div className="card">
          <h2 className="card-title">Optional BPM analysis</h2>
          <StatusStrip tone="info">
            This page reviews values already in CrateIQ’s index. A dedicated missing-data-only BPM analysis workflow is not exposed here yet. Configure tool availability and preferences in <Link to="/settings#analysis-tools">Settings → Analysis &amp; tools</Link>; existing and Mixed In Key values remain protected.
          </StatusStrip>
        </div>
      </section>

      {/* Anomaly table */}
      <section className="section">
        <div className="card" style={{ padding: 0 }}>
          <div className="card-header" style={{ padding: '16px 20px' }}>
            <h2 className="card-title" style={{ marginBottom: 0 }}>
              Anomalies
              {pendingCount > 0 && (
                <span className="card-title-count">({pendingCount} pending)</span>
              )}
              {requeuedCount > 0 && (
                <span className="card-title-count" style={{ color: '#60a5fa' }}>
                  ({requeuedCount} queued)
                </span>
              )}
            </h2>
            <div className="filter-bar" style={{ margin: 0 }}>
              <select
                className="filter-select"
                value={filterStatus}
                onChange={e => setFilterStatus(e.target.value as FilterStatus)}
              >
                <option value="pending">Pending</option>
                <option value="all">All statuses</option>
                <option value="reviewed">Reviewed</option>
                <option value="ignored">Ignored</option>
                <option value="requeued">Requeued</option>
              </select>
              <select
                className="filter-select"
                value={filterReason}
                onChange={e => setFilterReason(e.target.value as FilterReason)}
              >
                <option value="">All reasons</option>
                <option value="missing_bpm">Missing BPM</option>
                <option value="too_low_10x">Too Low (10× error)</option>
                <option value="likely_halved">Likely Halved</option>
                <option value="likely_doubled">Likely Doubled</option>
                <option value="too_high">Too High</option>
              </select>
            </div>
          </div>

          {anomalies.length === 0 && !loading ? (
            <div style={{ padding: '20px 24px' }}>
              <EmptyState
                message={
                  filterStatus === 'pending'
                    ? 'No pending anomalies. Run a BPM check to scan the library.'
                    : 'No records match the current filters.'
                }
              />
            </div>
          ) : (
            <div className="table-wrapper">
              <table className="table table--anomalies">
                <thead>
                  <tr>
                    <th>Track</th>
                    <th>Current BPM</th>
                    <th>Suggested</th>
                    <th>Flag</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {anomalies.map(a => (
                    <AnomalyRow key={a.id} anomaly={a} onUpdate={handleUpdate} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
