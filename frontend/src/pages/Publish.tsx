import { useCallback, useEffect, useMemo, useState } from 'react'
import { Download, HardDrive, Loader2, RefreshCw, ShieldCheck } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchCrates } from '../api/crates'
import {
  confirmPublishExport,
  confirmPublishSync,
  fetchPublishOperations,
  fetchPublishReadiness,
  fetchPublishSyncStatus,
  previewPublishExport,
  previewPublishSync,
} from '../api/publish'
import type { CrateSummary } from '../types/crate'
import type {
  PublishExportPreview,
  PublishExportResult,
  PublishExportTarget,
  PublishOperationSummary,
  PublishReadiness,
  PublishSyncPreview,
  PublishSyncSource,
  PublishSyncStatus,
} from '../types/publish'
import Badge, { type BadgeTone } from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import StatusStrip from '../components/ui/StatusStrip'
import PageHeader from '../components/PageHeader'

const EXPORT_TARGETS: { value: PublishExportTarget; label: string }[] = [
  { value: 'm3u8', label: 'M3U8 (UTF-8 playlist)' },
  { value: 'm3u', label: 'M3U (playlist)' },
  { value: 'csv', label: 'CSV' },
  { value: 'json', label: 'JSON' },
  { value: 'rekordbox_xml', label: 'Rekordbox XML (staged, manual import)' },
  { value: 'serato', label: 'Serato handoff (staged M3U8 + manifest)' },
]

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.displayMessage : 'Could not complete that publish action. Please try again.'
}

function verificationTone(status: string | null | undefined): BadgeTone {
  if (status === 'verified') return 'succeeded'
  if (status === 'failed') return 'failed'
  return 'pending'
}

function operationStatusTone(status: string): BadgeTone {
  if (status === 'completed') return 'succeeded'
  if (status === 'running') return 'running'
  if (status === 'cancelled') return 'cancelled'
  return 'failed'
}

export default function Publish() {
  const [crates, setCrates] = useState<CrateSummary[]>([])
  const [crateId, setCrateId] = useState<number | null>(null)
  const [exportTarget, setExportTarget] = useState<PublishExportTarget>('m3u8')
  // The only supported value: derived from the active workspace, never user-editable.
  const syncSource: PublishSyncSource = 'library'

  const [readiness, setReadiness] = useState<PublishReadiness | null>(null)
  const [readinessLoading, setReadinessLoading] = useState(false)
  const [readinessError, setReadinessError] = useState<string | null>(null)

  const [exportPreview, setExportPreview] = useState<PublishExportPreview | null>(null)
  const [exportPreviewLoading, setExportPreviewLoading] = useState(false)
  const [exportPreviewError, setExportPreviewError] = useState<string | null>(null)
  const [exportConfirming, setExportConfirming] = useState(false)
  const [exportResult, setExportResult] = useState<PublishExportResult | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)

  const [syncPreview, setSyncPreview] = useState<PublishSyncPreview | null>(null)
  const [syncPreviewLoading, setSyncPreviewLoading] = useState(false)
  const [syncPreviewError, setSyncPreviewError] = useState<string | null>(null)
  const [syncConfirming, setSyncConfirming] = useState(false)
  const [syncStatus, setSyncStatus] = useState<PublishSyncStatus | null>(null)
  const [syncError, setSyncError] = useState<string | null>(null)

  const [operations, setOperations] = useState<PublishOperationSummary[]>([])
  const [operationsLoading, setOperationsLoading] = useState(true)
  const [refreshSignal, setRefreshSignal] = useState(0)

  useEffect(() => {
    fetchCrates()
      .then((next) => { setCrates(next); setCrateId((current) => current ?? next[0]?.id ?? null) })
      .catch(() => {})
  }, [])

  // A stale preview must never remain confirmable once the selection changes.
  useEffect(() => {
    setExportPreview(null); setExportPreviewError(null); setExportResult(null); setExportError(null)
  }, [crateId, exportTarget])
  useEffect(() => {
    setSyncPreview(null); setSyncPreviewError(null); setSyncStatus(null); setSyncError(null)
  }, [syncSource])

  const loadReadiness = useCallback(() => {
    if (!crateId) { setReadiness(null); return }
    setReadinessLoading(true); setReadinessError(null)
    fetchPublishReadiness(crateId, exportTarget, syncSource)
      .then(setReadiness)
      .catch((err) => setReadinessError(messageOf(err)))
      .finally(() => setReadinessLoading(false))
  }, [crateId, exportTarget, syncSource])

  useEffect(() => { loadReadiness() }, [loadReadiness])

  const loadOperations = useCallback(() => {
    setOperationsLoading(true)
    fetchPublishOperations(undefined, 20)
      .then(setOperations)
      .catch(() => {})
      .finally(() => setOperationsLoading(false))
  }, [])

  useEffect(() => { loadOperations() }, [loadOperations, refreshSignal])

  const runExportPreview = async () => {
    if (!crateId) return
    setExportPreviewLoading(true); setExportPreviewError(null); setExportResult(null); setExportError(null)
    try { setExportPreview(await previewPublishExport(crateId, exportTarget)) }
    catch (err) { setExportPreviewError(messageOf(err)); setExportPreview(null) }
    finally { setExportPreviewLoading(false) }
  }

  const runExportConfirm = async () => {
    if (!crateId || !exportPreview || exportPreview.blockers.length > 0) return
    setExportConfirming(true); setExportError(null)
    try {
      const result = await confirmPublishExport(crateId, exportTarget)
      setExportResult(result)
      setExportPreview(null) // a repeat export requires a fresh preview
      setRefreshSignal((n) => n + 1)
      loadReadiness()
    } catch (err) { setExportError(messageOf(err)) }
    finally { setExportConfirming(false) }
  }

  const runSyncPreview = async () => {
    setSyncPreviewLoading(true); setSyncPreviewError(null); setSyncStatus(null); setSyncError(null)
    try { setSyncPreview(await previewPublishSync(syncSource)) }
    catch (err) { setSyncPreviewError(messageOf(err)); setSyncPreview(null) }
    finally { setSyncPreviewLoading(false) }
  }

  const runSyncConfirm = async () => {
    if (!syncPreview || syncPreview.blockers.length > 0) return
    setSyncConfirming(true); setSyncError(null)
    try {
      const confirmed = await confirmPublishSync(syncSource)
      setSyncPreview(null) // a repeat sync requires a fresh preview
      setSyncStatus({
        operation_id: confirmed.operation_id, operation_type: 'sync', sync_source: syncSource,
        job_id: confirmed.job_id, status: 'running', job_status: 'pending',
        progress_current: null, progress_total: null, progress_percent: null,
        destination_relative: null, result: null, verification_status: null,
        verification_details: [], warnings: [], error_reason: null,
        created_at: new Date().toISOString(), started_at: null, finished_at: null,
      })
      setRefreshSignal((n) => n + 1)
    } catch (err) { setSyncError(messageOf(err)) }
    finally { setSyncConfirming(false) }
  }

  // Poll live status only while the underlying rsync job is still running.
  useEffect(() => {
    if (!syncStatus || syncStatus.status !== 'running') return
    const timer = window.setInterval(() => {
      fetchPublishSyncStatus(syncStatus.operation_id)
        .then((next) => {
          setSyncStatus(next)
          if (next.status !== 'running') { setRefreshSignal((n) => n + 1); loadReadiness() }
        })
        .catch(() => {})
    }, 1500)
    return () => window.clearInterval(timer)
  }, [syncStatus, loadReadiness])

  const exportConfirmDisabled = !exportPreview || exportPreview.blockers.length > 0 || exportConfirming
  const syncConfirmDisabled = !syncPreview || syncPreview.blockers.length > 0 || syncConfirming || syncStatus?.status === 'running'

  const selectedCrateLabel = useMemo(() => {
    const crate = crates.find((c) => c.id === crateId)
    return crate ? `${crate.name} (${crate.track_count} tracks)` : null
  }, [crates, crateId])

  return (
    <main className="page publish-page">
      <PageHeader
        title="Publish"
        subtitle="Guided export and SSD sync for one Manual Crate at a time — every write is previewed and explicitly confirmed, then verified."
      />

      <section className="card publish-card">
        <h2 className="card-title">1. Select &amp; validate</h2>
        {crates.length === 0 ? (
          <EmptyState icon={<Download size={22} />} title="No Manual Crates yet" message="Create a crate on the Manual Crates page before publishing." />
        ) : (
          <>
            <div className="publish-select-row">
              <label>Crate
                <select className="form-input" value={crateId ?? ''} onChange={(e) => setCrateId(Number(e.target.value) || null)}>
                  {crates.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.track_count})</option>)}
                </select>
              </label>
              <label>Export format
                <select className="form-input" value={exportTarget} onChange={(e) => setExportTarget(e.target.value as PublishExportTarget)}>
                  {EXPORT_TARGETS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </label>
              <span className="muted" style={{ alignSelf: 'flex-end', fontSize: 12 }}>
                Sync source: Library (derived from active workspace)
              </span>
              <button type="button" className="btn btn--ghost btn--sm" disabled={!crateId || readinessLoading} onClick={() => loadReadiness()}>
                {readinessLoading ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />} Recheck
              </button>
            </div>

            {readinessError && <StatusStrip tone="danger" role="alert">{readinessError}</StatusStrip>}

            {readiness && (
              <div className="publish-readiness">
                <div className="publish-readiness-badges">
                  <Badge tone={readiness.export_ready ? 'succeeded' : 'failed'}>Export {readiness.export_ready ? 'ready' : 'blocked'}</Badge>
                  <Badge tone={readiness.sync_ready ? 'succeeded' : 'failed'}>Sync {readiness.sync_ready ? 'ready' : 'blocked'}</Badge>
                  <span className="muted">
                    {selectedCrateLabel} · destination: <code className="lib-defs-path">{readiness.export_destination_category}</code>
                  </span>
                </div>
                {readiness.blockers.length > 0 && (
                  <StatusStrip tone="danger" details={readiness.blockers}>Blockers — resolve before publishing</StatusStrip>
                )}
                {readiness.warnings.length > 0 && (
                  <StatusStrip tone="warn" details={readiness.warnings}>Warnings</StatusStrip>
                )}
                {readiness.conflicts.length > 0 && (
                  <StatusStrip tone="info" details={readiness.conflicts}>Conflicts — informational only, existing files are never overwritten</StatusStrip>
                )}
                <p className="muted">A ready state is not an approval — export and sync below each still require their own explicit preview and confirmation.</p>
              </div>
            )}
          </>
        )}
      </section>

      <section className="card publish-card">
        <div className="card-title-row">
          <h2 className="card-title"><Download size={16} /> 2. Export</h2>
          <Badge tone="info">Separate from sync — you can stop here</Badge>
        </div>
        <div className="publish-actions">
          <button type="button" className="btn btn--ghost btn--sm" disabled={!crateId || exportPreviewLoading} onClick={() => void runExportPreview()}>
            {exportPreviewLoading ? <Loader2 size={13} className="spin" /> : null} Preview export
          </button>
          <button type="button" className="btn btn--primary btn--sm" disabled={exportConfirmDisabled} onClick={() => void runExportConfirm()}>
            {exportConfirming ? <Loader2 size={13} className="spin" /> : <ShieldCheck size={13} />} Confirm &amp; export
          </button>
        </div>

        {exportPreviewError && <StatusStrip tone="danger" role="alert">{exportPreviewError}</StatusStrip>}

        {exportPreview && (
          <>
            <dl className="lib-defs">
              <dt>Target path</dt><dd className="lib-defs-path">{exportPreview.target_path}</dd>
              <dt>Proposed filename</dt><dd>{exportPreview.proposed_filename}</dd>
              <dt>Track count</dt><dd>{exportPreview.track_count}</dd>
              <dt>Overwrite behavior</dt><dd>Never overwrites — always a new uniquely named file</dd>
            </dl>
            {exportPreview.blockers.length > 0 && <StatusStrip tone="danger" details={exportPreview.blockers}>Cannot export yet</StatusStrip>}
            {exportPreview.warnings.length > 0 && <StatusStrip tone="warn" details={exportPreview.warnings}>Warnings</StatusStrip>}
          </>
        )}

        {exportError && <StatusStrip tone="danger" role="alert">{exportError}</StatusStrip>}

        {exportResult && (
          <div className="publish-result">
            <div className="publish-result-head">
              <Badge tone={exportResult.written ? 'succeeded' : 'failed'}>{exportResult.written ? 'Written' : 'Not written'}</Badge>
              <Badge tone={verificationTone(exportResult.verification_status)}>Verification: {exportResult.verification_status}</Badge>
            </div>
            <p className="lib-defs-path">{exportResult.output_path}</p>
            {exportResult.verification_details.length > 0 && (
              <ul className="publish-detail-list">{exportResult.verification_details.map((d, i) => <li key={i}>{d}</li>)}</ul>
            )}
            {exportResult.warnings.length > 0 && <StatusStrip tone="warn" details={exportResult.warnings}>Warnings</StatusStrip>}
          </div>
        )}
      </section>

      <section className="card publish-card">
        <div className="card-title-row">
          <h2 className="card-title"><HardDrive size={16} /> 3. SSD sync</h2>
          <Badge tone="info">Separate operation, no --delete ever</Badge>
        </div>
        <div className="publish-actions">
          <button type="button" className="btn btn--ghost btn--sm" disabled={syncPreviewLoading} onClick={() => void runSyncPreview()}>
            {syncPreviewLoading ? <Loader2 size={13} className="spin" /> : null} Preview sync
          </button>
          <button type="button" className="btn btn--primary btn--sm" disabled={syncConfirmDisabled} onClick={() => void runSyncConfirm()}>
            {syncConfirming ? <Loader2 size={13} className="spin" /> : <ShieldCheck size={13} />} Confirm &amp; sync
          </button>
        </div>

        {syncPreviewError && <StatusStrip tone="danger" role="alert">{syncPreviewError}</StatusStrip>}

        {syncPreview && (
          <>
            <dl className="lib-defs">
              <dt>Source</dt><dd className="lib-defs-path">{syncPreview.source_path}</dd>
              <dt>Destination</dt><dd className="lib-defs-path">{syncPreview.dest_path}</dd>
              <dt>Destination mounted</dt><dd>{syncPreview.ssd_mounted ? 'Yes' : 'No'}</dd>
              <dt>Pending files</dt><dd>{syncPreview.file_count}{syncPreview.truncated ? ' (truncated list)' : ''}</dd>
            </dl>
            {syncPreview.files.length > 0 && (
              <ul className="publish-detail-list">{syncPreview.files.slice(0, 10).map((f) => <li key={f.path}>{f.path}</li>)}</ul>
            )}
            {syncPreview.blockers.length > 0 && <StatusStrip tone="danger" details={syncPreview.blockers}>Cannot sync yet</StatusStrip>}
            {syncPreview.warnings.length > 0 && <StatusStrip tone="warn" details={syncPreview.warnings}>Warnings</StatusStrip>}
          </>
        )}

        {syncError && <StatusStrip tone="danger" role="alert">{syncError}</StatusStrip>}

        {syncStatus && (
          <div className="publish-result">
            <div className="publish-result-head">
              <Badge tone={operationStatusTone(syncStatus.status)}>
                {syncStatus.status === 'running' ? 'Running' : syncStatus.status}
              </Badge>
              {syncStatus.status !== 'running' && (
                <Badge tone={verificationTone(syncStatus.verification_status)}>Verification: {syncStatus.verification_status ?? 'skipped'}</Badge>
              )}
            </div>
            {syncStatus.status === 'running' && syncStatus.progress_total != null && syncStatus.progress_total > 0 && (
              <div className="job-progress">
                <div className="job-progress-bar">
                  <div
                    className="job-progress-fill"
                    style={{ width: `${Math.min(100, Math.round(syncStatus.progress_percent ?? 0))}%` }}
                  />
                </div>
                <span className="job-progress-pct">{syncStatus.progress_current} of {syncStatus.progress_total} files</span>
              </div>
            )}
            {syncStatus.verification_details.length > 0 && (
              <ul className="publish-detail-list">{syncStatus.verification_details.map((d, i) => <li key={i}>{d}</li>)}</ul>
            )}
            {syncStatus.error_reason && <StatusStrip tone="danger">{syncStatus.error_reason}</StatusStrip>}
          </div>
        )}
      </section>

      <section className="card publish-card">
        <div className="card-title-row">
          <h2 className="card-title">Recent publish operations</h2>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => loadOperations()} disabled={operationsLoading}>
            <RefreshCw size={13} /> Refresh
          </button>
        </div>
        {operationsLoading ? (
          <p className="muted">Loading…</p>
        ) : operations.length === 0 ? (
          <EmptyState title="No confirmed publish operations yet" message="Confirmed exports and syncs from above will appear here." />
        ) : (
          <div className="table-wrapper">
            <table className="table table--jobs">
              <thead>
                <tr><th>Type</th><th>Status</th><th>Scope</th><th>Verification</th><th className="nowrap">Started</th></tr>
              </thead>
              <tbody>
                {operations.map((op) => (
                  <tr key={op.id}>
                    <td>{op.operation_type === 'export' ? `Export (${op.export_target})` : `Sync (${op.sync_source})`}</td>
                    <td><Badge tone={operationStatusTone(op.status)}>{op.status}</Badge></td>
                    <td className="muted">{op.crate_name ?? op.scope ?? '—'}</td>
                    <td>{op.verification_status ? <Badge tone={verificationTone(op.verification_status)}>{op.verification_status}</Badge> : '—'}</td>
                    <td className="muted nowrap">{op.started_at ? new Date(op.started_at).toLocaleString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  )
}
