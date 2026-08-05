import { Fragment, useState } from 'react'
import { RefreshCw, Loader2, XCircle, ScrollText } from 'lucide-react'
import { useJobs } from '../hooks/useJobs'
import { cancelJob } from '../api/jobs'
import { ApiError } from '../api/client'
import StatusBadge from '../components/StatusBadge'
import PageHeader from '../components/PageHeader'
import LogModal from '../components/LogModal'
import EmptyState from '../components/ui/EmptyState'
import StatusStrip from '../components/ui/StatusStrip'
import AnalysisJobsCatalog from '../components/analysis/AnalysisJobsCatalog'
import { isActive } from '../types/job'
import type { Job } from '../types/job'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month:  'short',
    day:    'numeric',
    hour:   '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatDuration(startedAt: string | null, finishedAt: string | null): string {
  if (!startedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end   = finishedAt ? new Date(finishedAt).getTime() : Date.now()
  const sec   = Math.floor((end - start) / 1000)
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}m ${s}s`
}

// ---------------------------------------------------------------------------
// Progress bar — used inline in running rows
// ---------------------------------------------------------------------------

function JobProgress({ job }: { job: Job }) {
  if (job.status === 'pending') {
    return (
      <div className="job-progress-indeterminate">
        <div className="job-progress-indeterminate-fill" />
      </div>
    )
  }
  if (job.progress_percent == null) return null

  const pct = Math.min(100, Math.max(0, job.progress_percent))
  return (
    <div className="job-progress">
      <div className="job-progress-bar">
        <div className="job-progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="job-progress-pct">{pct.toFixed(0)}%</span>
      {job.progress_message && (
        <span className="job-progress-msg">{job.progress_message}</span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Jobs table
// ---------------------------------------------------------------------------

interface JobsTableProps {
  jobs:        Job[]
  onViewLogs:  (job: Job) => void
  onCancelled: () => void
}

function JobsTable({ jobs, onViewLogs, onCancelled }: JobsTableProps) {
  const [cancellingIds, setCancellingIds] = useState<Set<string>>(new Set())
  const [cancelErrors,  setCancelErrors]  = useState<Record<string, string>>({})

  if (jobs.length === 0) {
    return <EmptyState title="No pipeline jobs recorded" message="Analysis previews are intentionally not persisted as pipeline jobs." />
  }

  async function handleCancel(jobId: string) {
    setCancellingIds((prev) => new Set(prev).add(jobId))
    setCancelErrors((prev)  => { const n = { ...prev }; delete n[jobId]; return n })
    try {
      await cancelJob(jobId)
      onCancelled()
    } catch (e) {
      const msg = e instanceof ApiError ? e.displayMessage : e instanceof Error ? e.message : String(e)
      setCancelErrors((prev) => ({ ...prev, [jobId]: msg }))
    } finally {
      setCancellingIds((prev) => { const n = new Set(prev); n.delete(jobId); return n })
    }
  }

  return (
    <div className="table-wrapper">
      <table className="table table--jobs">
        <thead>
          <tr>
            <th style={{ width: 76 }}>ID</th>
            <th>Command</th>
            <th>Args</th>
            <th style={{ width: 96 }}>Status</th>
            <th style={{ minWidth: 180 }}>Progress</th>
            <th className="nowrap">Started</th>
            <th className="nowrap">Duration</th>
            <th style={{ width: 32 }}>Exit</th>
            <th style={{ width: 110 }}></th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const cancellable = isActive(job)
            const cancelling  = cancellingIds.has(job.id)
            const cancelErr   = cancelErrors[job.id]
            const rowClass    = [
              job.status === 'running'   ? 'row--running'   : '',
              job.status === 'failed'    ? 'row--failed'    : '',
              job.status === 'cancelled' ? 'row--cancelled' : '',
            ].filter(Boolean).join(' ')

            return (
              <Fragment key={job.id}>
                <tr className={rowClass}>
                  <td>
                    <code className="job-id">{job.id.slice(0, 8)}</code>
                  </td>
                  <td>
                    <code className="job-command">{job.command}</code>
                  </td>
                  <td>
                    <code className="job-args">
                      {job.args.length > 0 ? job.args.join(' ') : <span className="muted">—</span>}
                    </code>
                  </td>
                  <td><StatusBadge status={job.status} /></td>
                  <td><JobProgress job={job} /></td>
                  <td className="muted nowrap td-timestamp">{formatDateTime(job.started_at)}</td>
                  <td className="muted td-duration">{formatDuration(job.started_at, job.finished_at)}</td>
                  <td className="td-exit">
                    {job.exit_code !== null
                      ? <code className={job.exit_code !== 0 ? 'text--error' : 'text--ok'}>{job.exit_code}</code>
                      : <span className="muted">—</span>
                    }
                  </td>
                  <td>
                    <div className="td-actions">
                      <button
                        className="btn btn--ghost btn--xs"
                        onClick={() => onViewLogs(job)}
                        title="View logs"
                      >
                        <ScrollText size={12} />
                        Logs
                      </button>
                      {cancellable && (
                        <button
                          className="btn btn--danger btn--xs"
                          onClick={() => handleCancel(job.id)}
                          disabled={cancelling}
                          title="Send SIGTERM"
                        >
                          {cancelling
                            ? <Loader2 size={12} className="spin" />
                            : <XCircle size={12} />
                          }
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
                {cancelErr && (
                  <tr>
                    <td colSpan={9} className="td-cancel-error">
                      Cancel failed: {cancelErr}
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Jobs() {
  const { jobs, loading, error, refresh } = useJobs()
  const [logJob, setLogJob] = useState<Job | null>(null)

  const activeCount = jobs.filter(isActive).length

  return (
    <div className="page">
      <PageHeader
        title="Analysis Jobs"
        subtitle="Optional advanced workflows. Core CrateIQ works without these tools."
        badge={activeCount > 0
          ? <span className="live-indicator">{activeCount} active</span>
          : undefined
        }
        actions={
          <button className="btn btn--ghost btn--sm" onClick={refresh}>
            <RefreshCw size={13} />
            Refresh
          </button>
        }
      />

      {error && (
        <StatusStrip tone="danger" role="alert">
          <strong>Error:</strong> {error}
        </StatusStrip>
      )}

      <section className="section">
        <div className="card">
          <AnalysisJobsCatalog />
        </div>
      </section>

      <section className="section">
        <div className="card">
          <h2 className="card-title">
            Pipeline Job History
            {!loading && <span className="card-title-count">({jobs.length})</span>}
          </h2>
          {loading
            ? <p className="muted">Loading…</p>
            : <JobsTable jobs={jobs} onViewLogs={setLogJob} onCancelled={refresh} />
          }
        </div>
      </section>

      {logJob && (
        <LogModal job={logJob} onClose={() => setLogJob(null)} />
      )}
    </div>
  )
}
