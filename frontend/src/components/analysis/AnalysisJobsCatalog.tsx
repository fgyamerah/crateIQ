import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, Eye, RefreshCw, Wrench } from 'lucide-react'
import { ApiError } from '../../api/client'
import { fetchAnalysisJobHistory, fetchAnalysisJobs, previewAnalysisJob, runBpmAnalysis } from '../../api/analysis'
import type { AnalysisJobDefinition, AnalysisJobPreview, AnalysisJobStatus, AnalysisJobType, BpmAnalysisRunResult } from '../../types/analysis'
import Badge from '../ui/Badge'
import EmptyState from '../ui/EmptyState'
import KpiCard from '../ui/KpiCard'
import StatusStrip from '../ui/StatusStrip'

function tone(status: AnalysisJobStatus) {
  return status === 'ready' ? 'succeeded' : status === 'missing_tool' ? 'failed' : 'pending'
}

function label(status: AnalysisJobStatus) {
  return status === 'missing_tool' ? 'Missing tool' : status === 'coming_soon' ? 'Preview only' : status === 'disabled' ? 'No tracks yet' : 'Ready to preview'
}

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.displayMessage : 'Could not load Analysis Jobs.'
}

export default function AnalysisJobsCatalog() {
  const [jobs, setJobs] = useState<AnalysisJobDefinition[]>([])
  const [preview, setPreview] = useState<AnalysisJobPreview | null>(null)
  const [historyMessage, setHistoryMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const [previewing, setPreviewing] = useState<AnalysisJobType | null>(null)
  const [bpmConfirmed, setBpmConfirmed] = useState(false)
  const [bpmLimit, setBpmLimit] = useState(10)
  const [bpmRunning, setBpmRunning] = useState(false)
  const [bpmResult, setBpmResult] = useState<BpmAnalysisRunResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [catalog, history] = await Promise.all([fetchAnalysisJobs(), fetchAnalysisJobHistory()])
      setJobs(catalog.jobs)
      setHistoryMessage(history.message)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const showPreview = async (jobType: AnalysisJobType) => {
    setPreviewing(jobType)
    setError(null)
    try {
      const result = await previewAnalysisJob(jobType)
      setPreview(result)
      if (jobType === 'bpm_analysis') {
        setBpmConfirmed(false)
        setBpmResult(null)
      }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setPreviewing(null)
    }
  }

  const runBpm = async () => {
    setBpmRunning(true)
    setError(null)
    try {
      const result = await runBpmAnalysis(bpmLimit)
      setBpmResult(result)
      setBpmConfirmed(false)
      setPreview(null)
      await load()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBpmRunning(false)
    }
  }

  const ready = jobs.filter((job) => job.status === 'ready').length
  const gated = jobs.filter((job) => job.status === 'missing_tool').length

  return (
    <>
      <div className="analysis-jobs-kpis">
        <KpiCard tone="cyan" label="Optional workflows" value={jobs.length || '—'} sub="Core import, crates, and exports stay available" />
        <KpiCard tone="emerald" label="Ready to preview" value={ready} sub="MIK metadata source review" />
        <KpiCard tone="violet" label="Tool-gated" value={gated} sub="Only those workflows are disabled" />
      </div>
      <StatusStrip tone="info" icon={<CheckCircle2 size={15} />}>No tag writes. Existing Mixed In Key-compatible values are preserved. Candidate previews are missing-data-only and write nothing.</StatusStrip>
      <div className="analysis-jobs-head">
        <div><h2 className="card-title">Optional workflows</h2><p className="muted">Preview candidates first. A runner appears only after it has a safe, explicit DB-only contract.</p></div>
        <button className="btn btn--ghost btn--sm" onClick={() => void load()} disabled={loading}><RefreshCw size={13} /> Refresh</button>
      </div>
      {error && <StatusStrip tone="danger">{error}</StatusStrip>}
      {loading ? <p className="muted">Loading optional workflows…</p> : <div className="analysis-jobs-grid">
        {jobs.map((job) => <article className="analysis-job-card" id={job.type.replace(/_/g, '-')} key={job.type}>
          <div className="analysis-job-card-head"><div><h3>{job.label}</h3><p>{job.message}</p></div><Badge tone={tone(job.status)}>{label(job.status)}</Badge></div>
          <div className="analysis-job-meta"><span><strong>{job.candidate_count}</strong> candidate{job.candidate_count === 1 ? '' : 's'}</span><span>{job.required_source ?? job.required_tools.join(' + ')}</span></div>
          <p className="settings-note">Writes: {job.write_behavior}. {job.safety.join(' · ').replace(/_/g, ' ')}.</p>
          <div className="settings-action-row">
            <button className="btn btn--ghost btn--sm" disabled={previewing === job.type || job.status === 'disabled'} onClick={() => void showPreview(job.type)}><Eye size={13} /> {previewing === job.type ? 'Loading…' : 'Preview'}</button>
            {job.type === 'mixed_in_key_coverage' ? <Link className="btn btn--primary btn--sm" to="/settings#analysis-tools">Open MIK import</Link> : job.type === 'bpm_analysis' && job.runner_implemented ? <span className="analysis-job-run-hint">Preview, then confirm below</span> : <Link className="btn btn--ghost btn--sm" to="/settings#analysis-tools"><Wrench size={13} /> {job.status === 'missing_tool' ? 'Tool setup' : 'Runner pending'}</Link>}
          </div>
        </article>)}
      </div>}
      {preview && <section className="analysis-job-preview" aria-live="polite">
        <div className="settings-import-result-head"><div><h2 className="card-title">{preview.job.label} preview</h2><p className="muted">{preview.candidate_count} candidates from {preview.total_tracks} indexed tracks.</p></div><Badge tone={tone(preview.job.status)}>{label(preview.job.status)}</Badge></div>
        <StatusStrip tone="info">Expected behavior: {preview.expected_write_behavior}. {preview.runner_implemented ? 'A safe runner is available.' : 'No runner was started.'}</StatusStrip>
        {preview.samples.length > 0 ? <div className="settings-import-samples"><strong>Sample candidates</strong><ul>{preview.samples.map((item) => <li key={item.track_id}><code>{item.relative_path ?? item.filename}</code>{item.artist || item.title ? ` · ${[item.artist, item.title].filter(Boolean).join(' — ')}` : ''}{item.bpm ? ` · ${item.bpm} BPM` : ''}{item.key_camelot || item.key_musical ? ` · ${item.key_camelot ?? item.key_musical}` : ''}</li>)}</ul></div> : <EmptyState title="No candidates" message="This workflow has no eligible indexed tracks yet." />}
        {preview.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}
        {preview.job.type === 'bpm_analysis' && preview.runner_implemented && preview.candidate_count > 0 && <div className="analysis-bpm-confirm">
          <label className="form-check"><input type="checkbox" checked={bpmConfirmed} onChange={(event) => setBpmConfirmed(event.target.checked)} disabled={bpmRunning} /> I understand this writes BPM only to CrateIQ’s local index.</label>
          <label className="settings-note">Track limit<select className="form-input" value={bpmLimit} onChange={(event) => setBpmLimit(Number(event.target.value))} disabled={bpmRunning}><option value={1}>1 track</option><option value={3}>3 tracks</option><option value={10}>10 tracks</option><option value={25}>25 tracks</option><option value={50}>50 tracks</option></select></label>
          <button className="btn btn--primary btn--sm" disabled={!bpmConfirmed || bpmRunning} onClick={() => void runBpm()}>{bpmRunning ? 'Analyzing BPM…' : `Run aubio for up to ${bpmLimit}`}</button>
        </div>}
      </section>}
      {bpmResult && <section className="analysis-job-preview" aria-live="polite"><div className="settings-import-result-head"><div><h2 className="card-title">BPM analysis result</h2><p className="muted">Aubio wrote only eligible BPM values to CrateIQ’s local index.</p></div><Badge tone="succeeded">Complete</Badge></div><div className="settings-import-summary"><span><strong>{bpmResult.updated}</strong> updated</span><span><strong>{bpmResult.skipped}</strong> skipped</span><span><strong>{bpmResult.failed}</strong> failed</span><span><strong>{bpmResult.remaining_missing_bpm}</strong> remaining missing</span></div>{bpmResult.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}</section>}
      <section className="analysis-job-history">
        <h2 className="card-title">Analysis history</h2>
        <EmptyState title="No analysis runs yet" message={historyMessage || 'History starts when an explicit safe runner is implemented.'} />
      </section>
    </>
  )
}
