import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { RefreshCw, Loader2 } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchJobLogs, fetchJob, cancelJob } from '../api/jobs'
import { fetchSyncConfig, previewSync, startSync, fetchSyncJobs } from '../api/sync'
import type { SyncConfigResponse, SyncPreviewResponse } from '../types/sync'
import type { Job } from '../types/job'
import { isActive } from '../types/job'
import Badge from '../components/ui/Badge'
import ErrorBanner from '../components/ErrorBanner'
import PageHeader from '../components/PageHeader'
import StatusBadge from '../components/StatusBadge'
import StatusStrip from '../components/ui/StatusStrip'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

function fmtDuration(start: string | null, end: string | null): string {
  if (!start) return '—'
  const s = Math.floor((new Date(end ?? Date.now()).getTime() - new Date(start).getTime()) / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

// ---------------------------------------------------------------------------
// SSD mount warning banner
// ---------------------------------------------------------------------------

function MountWarning() {
  return (
    <StatusStrip tone="danger" role="alert">
      <strong>SSD not mounted.</strong>
      {' '}Connect the drive and mount it before syncing.
    </StatusStrip>
  )
}

// ---------------------------------------------------------------------------
// Config panel (read-only display)
// ---------------------------------------------------------------------------

function destinationStatusTone(status: SyncConfigResponse['destination_status']) {
  if (status === 'ready') return 'succeeded' as const
  if (status === 'needs_setup') return 'pending' as const
  return 'failed' as const
}

function destinationStatusLabel(status: SyncConfigResponse['destination_status']) {
  if (status === 'ready') return 'Ready'
  if (status === 'needs_setup') return 'Needs Setup'
  if (status === 'not_mounted') return 'Not Mounted'
  return 'Unsafe'
}

function ConfigPanel({ cfg }: { cfg: SyncConfigResponse }) {
  const srcPath = cfg.sources.library || '—'
  return (
    <div className="sync-config-panel">
      <div className="sync-path-row">
        <span className="sync-path-label">Source</span>
        <code className="sync-path-code">{srcPath}</code>
        <span className="muted" style={{ fontSize: 11 }}>derived from active workspace</span>
      </div>
      <div className="sync-path-row">
        <span className="sync-path-label">Destination</span>
        <code className="sync-path-code">{cfg.dest ?? 'Not configured'}</code>
        <Badge tone={destinationStatusTone(cfg.destination_status)}>{destinationStatusLabel(cfg.destination_status)}</Badge>
        {cfg.destination_status === 'needs_setup' && (
          <Link className="btn btn--ghost btn--xs" to="/settings#publish-sync">Configure in Settings</Link>
        )}
      </div>
      <div className="sync-path-row">
        <span className="sync-path-label">rsync</span>
        <code className="sync-path-code sync-path-code--muted">{cfg.rsync_bin}</code>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Preview results
// ---------------------------------------------------------------------------

const PREVIEW_PAGE = 50

function PreviewResults({ preview }: { preview: SyncPreviewResponse }) {
  const [page, setPage] = useState(0)

  useEffect(() => setPage(0), [preview])

  const { files, file_count, truncated, summary, warnings } = preview
  const totalPages = Math.max(1, Math.ceil(files.length / PREVIEW_PAGE))
  const safePage   = Math.min(page, totalPages - 1)
  const slice      = files.slice(safePage * PREVIEW_PAGE, (safePage + 1) * PREVIEW_PAGE)

  return (
    <div className="preview-results">
      {warnings.map((w, i) => (
        <StatusStrip key={i} tone="warn" className="sync-warning-item">{w}</StatusStrip>
      ))}

      {file_count === 0 && warnings.length === 0 && (
        <p className="muted">
          No files need to be transferred — destination is up to date.
        </p>
      )}

      {file_count > 0 && (
        <>
          <div className="preview-stat-row">
            <span className="preview-stat-value">{file_count.toLocaleString()}</span>
            <span className="preview-stat-label">
              {truncated ? `files (showing first ${files.length})` : 'files to transfer'}
            </span>
          </div>

          {summary && (
            <code className="preview-summary">{summary}</code>
          )}

          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead>
                <tr><th>Path</th></tr>
              </thead>
              <tbody>
                {slice.map((f, i) => (
                  <tr key={i}>
                    <td>
                      <code className="preview-path">
                        {f.is_dir ? (
                          <span className="preview-dir-icon">▸ </span>
                        ) : null}
                        {f.path}
                      </code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="pagination">
              <button
                className="btn btn--ghost btn--sm"
                disabled={safePage === 0}
                onClick={() => setPage(safePage - 1)}
              >← Prev</button>
              <span className="muted">
                {safePage + 1} / {totalPages}
              </span>
              <button
                className="btn btn--ghost btn--sm"
                disabled={safePage >= totalPages - 1}
                onClick={() => setPage(safePage + 1)}
              >Next →</button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Progress bar
// ---------------------------------------------------------------------------

function ProgressBar({
  percent,
  current,
  total,
  message,
}: {
  percent:  number
  current:  number | null
  total:    number | null
  message:  string | null
}) {
  const pct = Math.min(100, Math.max(0, percent))
  return (
    <div className="progress-wrap">
      <div className="progress-bar-outer">
        <div
          className="progress-bar-fill"
          style={{ width: `${pct}%` }}
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        />
      </div>
      <div className="progress-labels">
        <span className="progress-pct">{pct.toFixed(1)}%</span>
        {message && <span className="progress-msg">{message}</span>}
        {current != null && total != null && !message && (
          <span className="progress-msg">{current}/{total} files</span>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Inline log viewer
// ---------------------------------------------------------------------------

function LogInline({ jobId, active }: { jobId: string; active: boolean }) {
  const [logs, setLogs] = useState('')

  const load = useCallback(async () => {
    try { setLogs(await fetchJobLogs(jobId, 80)) } catch { /* ignore */ }
  }, [jobId])

  useEffect(() => {
    load()
    if (!active) return
    const t = setInterval(load, 2000)
    return () => clearInterval(t)
  }, [active, load])

  if (!logs) return null
  return <pre className="log-inline">{logs}</pre>
}

// ---------------------------------------------------------------------------
// Active job panel
// ---------------------------------------------------------------------------

function ActiveJobPanel({
  job: initialJob,
  onFinished,
  onCancel,
}: {
  job:        Job
  onFinished: () => void
  onCancel:   (jobId: string) => void
}) {
  const [job,          setJob]          = useState<Job>(initialJob)
  const [cancelling,   setCancelling]   = useState(false)
  const [cancelErr,    setCancelErr]    = useState<string | null>(null)
  const active = isActive(job)

  // Poll for job updates while running
  useEffect(() => {
    if (!active) return
    const t = setInterval(async () => {
      try {
        const updated = await fetchJob(job.id)
        setJob(updated)
        if (!isActive(updated)) onFinished()
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(t)
  }, [active, job.id, onFinished])

  async function handleCancel() {
    setCancelling(true)
    setCancelErr(null)
    try {
      await onCancel(job.id)
    } catch (e) {
      setCancelErr(e instanceof ApiError ? e.displayMessage : String(e))
    } finally {
      setCancelling(false)
    }
  }

  return (
    <div className="bpm-job-panel">
      <div className="bpm-job-header">
        <span className="muted" style={{ fontSize: 12 }}>
          Job <code>{job.id.slice(0, 8)}</code>
        </span>
        <StatusBadge status={job.status} />
        {active && <span className="live-indicator">live · polling 2s</span>}
        {active && (
          <button
            className="btn btn--danger btn--sm"
            onClick={handleCancel}
            disabled={cancelling}
          >
            {cancelling ? 'Cancelling…' : 'Cancel'}
          </button>
        )}
      </div>

      {cancelErr && (
        <StatusStrip tone="danger" role="alert" className="sync-warning-item">
          Cancel failed: {cancelErr}
        </StatusStrip>
      )}

      {/* Progress bar (only for jobs that have progress data) */}
      {job.progress_percent != null && (
        <ProgressBar
          percent={job.progress_percent}
          current={job.progress_current}
          total={job.progress_total}
          message={job.progress_message}
        />
      )}

      <LogInline jobId={job.id} active={active} />

      {job.status === 'succeeded' && (
        <p style={{ color: '#22c55e', fontSize: 13, marginTop: 8 }}>
          Sync complete.
        </p>
      )}
      {job.status === 'failed' && (
        <p style={{ color: '#ef4444', fontSize: 13, marginTop: 8 }}>
          Sync failed — see log above.
        </p>
      )}
      {job.status === 'cancelled' && (
        <p style={{ color: '#9ca3af', fontSize: 13, marginTop: 8 }}>
          Sync cancelled.
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Past sync jobs table
// ---------------------------------------------------------------------------

function SyncHistory({ jobs, onRefresh, loading }: { jobs: Job[]; onRefresh: () => void; loading: boolean }) {
  if (jobs.length === 0) {
    return <p className="muted empty-hint">No sync jobs yet.</p>
  }
  return (
    <>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Job ID</th>
              <th>Source</th>
              <th>Status</th>
              <th>Started</th>
              <th>Duration</th>
              <th>Exit</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td><code style={{ fontSize: 11 }}>{j.id.slice(0, 12)}…</code></td>
                <td className="muted">{j.args[0] ?? '—'}</td>
                <td><StatusBadge status={j.status} /></td>
                <td className="muted">{fmtDate(j.started_at)}</td>
                <td className="muted">{fmtDuration(j.started_at, j.finished_at)}</td>
                <td className="muted">
                  {j.exit_code != null ? (
                    <code className={j.exit_code !== 0 ? 'text--error' : ''}>
                      {j.exit_code}
                    </code>
                  ) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 8 }}>
        <button className="btn btn--ghost btn--sm" onClick={onRefresh} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SsdSync() {
  const [cfg,           setCfg]          = useState<SyncConfigResponse | null>(null)
  const [cfgErr,        setCfgErr]       = useState<string | null>(null)

  const [previewing,    setPreviewing]   = useState(false)
  const [preview,       setPreview]      = useState<SyncPreviewResponse | null>(null)
  const [previewErr,    setPreviewErr]   = useState<string | null>(null)

  const [running,       setRunning]      = useState(false)
  const [runErr,        setRunErr]       = useState<string | null>(null)
  const [allowDelete,   setAllowDelete]  = useState(false)

  const [activeJob,     setActiveJob]    = useState<Job | null>(null)

  const [histJobs,      setHistJobs]     = useState<Job[]>([])
  const [histLoading,   setHistLoading]  = useState(false)

  // Load config + history on mount
  useEffect(() => {
    fetchSyncConfig()
      .then(setCfg)
      .catch((e) => setCfgErr(e instanceof ApiError ? e.displayMessage : String(e)))
    loadHistory()
  }, [])

  const loadHistory = useCallback(async () => {
    setHistLoading(true)
    try { setHistJobs(await fetchSyncJobs()) } catch { /* ignore */ }
    finally { setHistLoading(false) }
  }, [])

  async function handlePreview() {
    setPreviewing(true)
    setPreviewErr(null)
    setPreview(null)
    try {
      setPreview(await previewSync({ source: 'library' }))
    } catch (e) {
      setPreviewErr(e instanceof ApiError ? e.displayMessage : String(e))
    } finally {
      setPreviewing(false)
    }
  }

  async function handleRunSync() {
    setRunning(true)
    setRunErr(null)
    try {
      const resp = await startSync({ source: 'library', allow_delete: allowDelete })
      const job  = await fetchJob(resp.job_id)
      setActiveJob(job)
      loadHistory()
    } catch (e) {
      setRunErr(e instanceof ApiError ? e.displayMessage : String(e))
    } finally {
      setRunning(false)
    }
  }

  async function handleCancel(jobId: string) {
    await cancelJob(jobId)
    // Refresh the active job state
    const updated = await fetchJob(jobId)
    setActiveJob(updated)
  }

  const ssdMounted = cfg?.ssd_mounted ?? true

  return (
    <div className="page">
      <PageHeader
        title="SSD Sync"
        subtitle="Rsync the working library to the external SSD. The SSD is the Rekordbox deployment target — never the source of truth."
        badge={cfg && !cfg.ssd_mounted
          ? <span className="badge badge--failed">not mounted</span>
          : cfg
          ? <span className="badge badge--succeeded">mounted</span>
          : undefined
        }
        actions={
          <button className="btn btn--ghost btn--sm" onClick={loadHistory}>
            <RefreshCw size={13} />
            Refresh
          </button>
        }
      />

      {cfgErr && <ErrorBanner message={cfgErr} />}

      {/* ------------------------------------------------------------------ */}
      {/* Config + source picker                                               */}
      {/* ------------------------------------------------------------------ */}
      <section className="section">
        <div className="card">
          <h2 className="card-title">Sync Configuration</h2>

          {!ssdMounted && <MountWarning />}

          {cfg ? (
            <ConfigPanel cfg={cfg} />
          ) : !cfgErr && (
            <p className="muted">Loading config…</p>
          )}

          <div style={{ marginTop: 14, display: 'flex', gap: 10 }}>
            <button
              className="btn btn--ghost"
              onClick={handlePreview}
              disabled={previewing || !cfg}
            >
              {previewing
                ? <><Loader2 size={13} className="spin" /> Scanning…</>
                : preview ? 'Re-scan' : 'Preview Changes'
              }
            </button>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* Preview results                                                      */}
      {/* ------------------------------------------------------------------ */}
      {previewErr && (
        <section className="section">
          <div className="card">
            <ErrorBanner message={previewErr} onDismiss={() => setPreviewErr(null)} />
          </div>
        </section>
      )}

      {preview && (
        <section className="section">
          <div className="card">
            <h2 className="card-title">
              Preview
              <span className="card-title-count">
                ({preview.file_count.toLocaleString()} files
                {preview.truncated ? '+' : ''})
              </span>
            </h2>
            <PreviewResults preview={preview} />
          </div>
        </section>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Run sync                                                             */}
      {/* ------------------------------------------------------------------ */}
      <section className="section">
        <div className="card">
          <h2 className="card-title">Run Sync</h2>

          {runErr && (
            <ErrorBanner message={runErr} onDismiss={() => setRunErr(null)} />
          )}

          <div className="sync-run-panel">
            <label className="form-check sync-delete-check">
              <input
                type="checkbox"
                checked={allowDelete}
                onChange={(e) => setAllowDelete(e.target.checked)}
                disabled={running}
              />
              <span>
                Allow delete{' '}
                <span className="muted" style={{ fontSize: 11 }}>
                  (removes files from SSD that are no longer in source — destructive)
                </span>
              </span>
            </label>

            {allowDelete && (
              <div className="sync-delete-warning">
                Delete mode enabled. Files on the SSD that are not in the source will be removed.
              </div>
            )}

            <button
              className="btn btn--primary"
              onClick={handleRunSync}
              disabled={running || !ssdMounted || !cfg}
              title={!ssdMounted ? 'SSD not mounted' : undefined}
            >
              {running ? 'Starting…' : 'Run Sync'}
            </button>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* Active job                                                           */}
      {/* ------------------------------------------------------------------ */}
      {activeJob && (
        <section className="section">
          <div className="card">
            <h2 className="card-title">Active Sync Job</h2>
            <ActiveJobPanel
              job={activeJob}
              onFinished={loadHistory}
              onCancel={handleCancel}
            />
          </div>
        </section>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* History                                                              */}
      {/* ------------------------------------------------------------------ */}
      <section className="section">
        <div className="card">
          <h2 className="card-title">Sync History</h2>
          <SyncHistory
            jobs={histJobs}
            onRefresh={loadHistory}
            loading={histLoading}
          />
        </div>
      </section>
    </div>
  )
}
