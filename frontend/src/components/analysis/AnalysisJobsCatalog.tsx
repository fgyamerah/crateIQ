import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, Eye, RefreshCw, ShieldCheck, SlidersHorizontal, Wrench } from 'lucide-react'
import { ApiError } from '../../api/client'
import { fetchAnalysisJobHistory, fetchAnalysisJobs, previewAnalysisJob, runBpmAnalysis, runKeyAnalysis } from '../../api/analysis'
import type { AnalysisJobDefinition, AnalysisJobPreview, AnalysisJobStatus, AnalysisJobType, BpmAnalysisRunResult, KeyAnalysisRunResult } from '../../types/analysis'
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

function jobLabel(job: AnalysisJobDefinition) {
  if (job.type === 'duplicate_detection') return 'Preview only'
  if (job.type === 'audio_quality_probe') return job.status === 'missing_tool' ? 'Missing tool' : job.status === 'disabled' ? 'No tracks yet' : 'Probe only'
  return label(job.status)
}

type JobFilter = 'all' | 'runnable' | 'preview' | 'review' | 'missing'

function isRunnable(job: AnalysisJobDefinition) {
  return (job.type === 'bpm_analysis' || job.type === 'key_analysis') && job.status === 'ready' && job.runner_implemented
}

function jobMode(job: AnalysisJobDefinition) {
  if (job.status === 'missing_tool') return 'Missing tool'
  if (job.status === 'disabled') return 'No candidates'
  if (isRunnable(job)) return 'Runnable'
  if (job.type === 'beets_enrichment') return 'Review required'
  if (job.type === 'audio_quality_probe') return 'Probe only'
  if (job.type === 'duplicate_detection') return 'Preview only'
  if (job.type === 'mixed_in_key_coverage') return 'Preview + import'
  return jobLabel(job)
}

function safetyLabel(value: string) {
  return value.replace(/_/g, ' ')
}

function formatBytes(size: number | null) {
  if (size == null) return 'size unavailable'
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function formatDuration(seconds: number | null) {
  if (seconds == null) return 'duration unavailable'
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`
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
  const [keyConfirmed, setKeyConfirmed] = useState(false)
  const [keyRunning, setKeyRunning] = useState(false)
  const [keyResult, setKeyResult] = useState<KeyAnalysisRunResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<JobFilter>('all')

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
      if (jobType === 'key_analysis') { setKeyConfirmed(false); setKeyResult(null) }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setPreviewing(null)
    }
  }

  const runKey = async () => {
    setKeyRunning(true); setError(null)
    try { const result = await runKeyAnalysis(bpmLimit); setKeyResult(result); setKeyConfirmed(false); setPreview(null); await load() }
    catch (err) { setError(errorMessage(err)) } finally { setKeyRunning(false) }
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

  const runnable = jobs.filter(isRunnable).length
  const previewOnly = jobs.filter((job) => job.status === 'ready' && !isRunnable(job) && job.type !== 'beets_enrichment').length
  const gated = jobs.filter((job) => job.status === 'missing_tool').length
  const candidateTotal = jobs.reduce((total, job) => total + job.candidate_count, 0)
  const filteredJobs = jobs.filter((job) => {
    if (filter === 'runnable') return isRunnable(job)
    if (filter === 'preview') return job.status === 'ready' && !isRunnable(job) && job.type !== 'beets_enrichment'
    if (filter === 'review') return job.type === 'beets_enrichment'
    if (filter === 'missing') return job.status === 'missing_tool'
    return true
  })
  const filters: Array<{ id: JobFilter; label: string; count: number }> = [
    { id: 'all', label: 'All', count: jobs.length },
    { id: 'runnable', label: 'Runnable', count: runnable },
    { id: 'preview', label: 'Preview only', count: previewOnly },
    { id: 'review', label: 'Pending review', count: jobs.filter((job) => job.type === 'beets_enrichment').length },
    { id: 'missing', label: 'Missing tools', count: gated },
  ]

  return (
    <>
      <div className="analysis-jobs-kpis">
        <KpiCard tone="cyan" label="Optional workflows" value={jobs.length || '—'} sub="Core crateIQ stays available" />
        <KpiCard tone="emerald" label="Runnable now" value={runnable} sub="Preview and confirm first" />
        <KpiCard tone="violet" label="Preview only" value={previewOnly} sub="No changes can be made" />
        <KpiCard tone="coral" label="Missing tools" value={gated} sub={`${candidateTotal} candidates in view`} />
      </div>
      <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>Optional review workflows only. Core import, browsing, crates, preview, and exports never depend on these tools.</StatusStrip>
      <div className="analysis-jobs-policy-chips" aria-label="Analysis safety policies"><span><CheckCircle2 size={13} /> No tag writes</span><span><CheckCircle2 size={13} /> No file modifications</span><span><CheckCircle2 size={13} /> Preserve MIK</span><span><CheckCircle2 size={13} /> Missing-data-only</span><span><CheckCircle2 size={13} /> Preview-first</span><span><CheckCircle2 size={13} /> Local DB only</span></div>
      <div className="analysis-jobs-head">
        <div><h2 className="card-title">Optional workflows</h2><p className="muted">Preview candidates first. A runner appears only after it has a safe, explicit local-index-only contract.</p></div>
        <button className="btn btn--ghost btn--sm" onClick={() => void load()} disabled={loading}><RefreshCw size={13} /> Refresh</button>
      </div>
      <div className="analysis-job-filters" aria-label="Workflow filters"><SlidersHorizontal size={14} />{filters.map((item) => <button className={filter === item.id ? 'is-active' : ''} key={item.id} onClick={() => setFilter(item.id)} type="button">{item.label}<span>{item.count}</span></button>)}</div>
      {error && <StatusStrip tone="danger">{error}</StatusStrip>}
      {loading ? <p className="muted">Loading optional workflows…</p> : <div className="analysis-jobs-grid">
        {filteredJobs.map((job) => <article className="analysis-job-card" id={job.type.replace(/_/g, '-')} key={job.type}>
          <div className="analysis-job-card-head"><div><h3>{job.label}</h3><p>{job.message}</p></div><Badge tone={tone(job.status)}>{jobMode(job)}</Badge></div>
          <div className="analysis-job-meta"><span><strong>{job.candidate_count}</strong> candidate{job.candidate_count === 1 ? '' : 's'}</span><span>{job.required_source ?? (job.required_tools.join(' + ') || 'No optional tool')}</span></div>
          <div className="analysis-job-safety">{job.safety.map((item) => <span key={item}>{safetyLabel(item)}</span>)}</div>
          <p className="settings-note"><strong>Writes:</strong> {job.write_behavior}.</p>
          <div className="settings-action-row">
            <button className="btn btn--ghost btn--sm" disabled={previewing === job.type || job.status === 'disabled' || job.status === 'missing_tool'} onClick={() => void showPreview(job.type)}><Eye size={13} /> {previewing === job.type ? 'Loading…' : 'Preview'}</button>
            {job.type === 'mixed_in_key_coverage' ? <Link className="btn btn--primary btn--sm" to="/settings#analysis-tools">Open MIK import</Link> : (job.type === 'bpm_analysis' || job.type === 'key_analysis') && job.runner_implemented ? <span className="analysis-job-run-hint">Preview, then confirm below</span> : <Link className="btn btn--ghost btn--sm" to="/settings#analysis-tools"><Wrench size={13} /> {job.status === 'missing_tool' ? 'Tool setup' : job.type === 'beets_enrichment' ? 'Review required' : job.type === 'duplicate_detection' ? 'Resolution pending' : job.type === 'audio_quality_probe' ? 'Review pending' : 'Runner pending'}</Link>}
          </div>
        </article>)}
      </div>}
      {!loading && filteredJobs.length === 0 && <EmptyState title="No workflows in this view" message="Choose another filter to see the available optional workflows." />}
      {preview && <section className="analysis-job-preview" aria-live="polite">
        <div className="settings-import-result-head"><div><h2 className="card-title">{preview.job.label} preview</h2><p className="muted">{preview.candidate_count} candidates from {preview.total_tracks} indexed tracks. Preview results are not persisted.</p></div><Badge tone={tone(preview.job.status)}>{jobMode(preview.job)}</Badge></div>
        <StatusStrip tone="info">Expected behavior: {preview.expected_write_behavior}. {preview.runner_implemented ? 'A safe confirmed runner is available.' : 'No runner was started.'}</StatusStrip>
        <p className="analysis-preview-contract">Never touches music files, tags, MIK values, or live DJ application databases.</p>
        {preview.job.type === 'audio_quality_probe' ? <>
          <div className="settings-import-summary"><span><strong>{preview.summary?.total_tracks_checked ?? 0}</strong> checked</span><span><strong>{preview.summary?.probe_ok ?? 0}</strong> probe ok</span><span><strong>{preview.summary?.needs_review ?? 0}</strong> needs review</span></div>
          {preview.quality_probes.length > 0 ? <div className="settings-import-samples"><strong>Sample file probes</strong><ul>{preview.quality_probes.map((item) => <li key={item.track_id}><code>{item.relative_path ?? item.filename}</code> · {item.status.replace(/_/g, ' ')}<br /><span className="muted">{item.container ?? 'container unavailable'} · {item.codec ?? 'codec unavailable'} · {item.bitrate_kbps == null ? 'bitrate unavailable' : `${item.bitrate_kbps} kbps`} · {item.sample_rate_hz == null ? 'sample rate unavailable' : `${item.sample_rate_hz} Hz`} · {formatDuration(item.duration_sec)} · {formatBytes(item.file_size_bytes)}</span></li>)}</ul></div> : <EmptyState title="No files probed" message="No readable imported tracks were available for this bounded preview." />}
          {preview.next_step && <StatusStrip tone="warn">{preview.next_step}</StatusStrip>}
        </> : preview.job.type === 'duplicate_detection' ? <>
          <div className="settings-import-summary"><span><strong>{preview.summary?.total_tracks_checked ?? 0}</strong> checked</span><span><strong>{preview.summary?.duplicate_groups ?? 0}</strong> groups</span><span><strong>{preview.summary?.duplicate_candidates ?? 0}</strong> candidates</span></div>
          {preview.groups.length > 0 ? <div className="settings-import-samples"><strong>Duplicate groups</strong><ul>{preview.groups.map((group) => <li key={group.group_id}><strong>{group.group_id}</strong> · {group.reason} · {group.confidence} confidence<ul>{group.items.map((item) => <li key={item.track_id}><code>{item.relative_path ?? item.filename}</code>{item.artist || item.title ? ` · ${[item.artist, item.title].filter(Boolean).join(' — ')}` : ''} · {formatBytes(item.size_bytes)}</li>)}</ul></li>)}</ul></div> : <EmptyState title="No duplicate groups found" message="This preview found no duplicate groups in the bounded readable track set." />}
          {preview.next_step && <StatusStrip tone="warn">{preview.next_step}</StatusStrip>}
        </> : preview.samples.length > 0 ? <div className="settings-import-samples"><strong>Sample candidates</strong><ul>{preview.samples.map((item) => <li key={item.track_id}><code>{item.relative_path ?? item.filename}</code>{item.artist || item.title ? ` · ${[item.artist, item.title].filter(Boolean).join(' — ')}` : ''}{item.missing_fields.length ? ` · missing ${item.missing_fields.join(', ')}` : ''}{item.bpm ? ` · ${item.bpm} BPM` : ''}{item.key_camelot || item.key_musical ? ` · ${item.key_camelot ?? item.key_musical}` : ''}</li>)}</ul></div> : <EmptyState title="No candidates" message="This workflow has no eligible indexed tracks yet." />}
        {preview.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}
        {preview.job.type === 'bpm_analysis' && preview.runner_implemented && preview.candidate_count > 0 && <div className="analysis-bpm-confirm">
          <div className="analysis-run-contract"><strong>Confirm local-index update</strong><span>Missing BPM only · no tag writes · preserves MIK and trusted values.</span></div>
          <label className="form-check"><input type="checkbox" checked={bpmConfirmed} onChange={(event) => setBpmConfirmed(event.target.checked)} disabled={bpmRunning} /> I understand this writes BPM only to CrateIQ’s local index.</label>
          <label className="settings-note">Track limit<select className="form-input" value={bpmLimit} onChange={(event) => setBpmLimit(Number(event.target.value))} disabled={bpmRunning}><option value={1}>1 track</option><option value={3}>3 tracks</option><option value={10}>10 tracks</option><option value={25}>25 tracks</option><option value={50}>50 tracks</option></select></label>
          <button className="btn btn--primary btn--sm" disabled={!bpmConfirmed || bpmRunning} onClick={() => void runBpm()}>{bpmRunning ? 'Analyzing BPM…' : `Run aubio for up to ${bpmLimit}`}</button>
        </div>}
        {preview.job.type === 'key_analysis' && preview.runner_implemented && preview.candidate_count > 0 && <div className="analysis-bpm-confirm">
          <div className="analysis-run-contract"><strong>Confirm local-index update</strong><span>Missing key/Camelot only · no tag writes · preserves MIK and trusted values.</span></div>
          <label className="form-check"><input type="checkbox" checked={keyConfirmed} onChange={(event) => setKeyConfirmed(event.target.checked)} disabled={keyRunning} /> I understand this writes key/Camelot only to CrateIQ’s local index.</label>
          <label className="settings-note">Track limit<select className="form-input" value={bpmLimit} onChange={(event) => setBpmLimit(Number(event.target.value))} disabled={keyRunning}><option value={1}>1 track</option><option value={3}>3 tracks</option><option value={10}>10 tracks</option><option value={25}>25 tracks</option><option value={50}>50 tracks</option></select></label>
          <button className="btn btn--primary btn--sm" disabled={!keyConfirmed || keyRunning} onClick={() => void runKey()}>{keyRunning ? 'Analyzing key…' : `Run keyfinder for up to ${bpmLimit}`}</button>
        </div>}
      </section>}
      {bpmResult && <section className="analysis-job-preview" aria-live="polite"><div className="settings-import-result-head"><div><h2 className="card-title">BPM analysis result</h2><p className="muted">Aubio wrote only eligible BPM values to CrateIQ’s local index.</p></div><Badge tone="succeeded">Complete</Badge></div><div className="settings-import-summary"><span><strong>{bpmResult.analyzed}</strong> analyzed</span><span><strong>{bpmResult.updated}</strong> updated</span><span><strong>{bpmResult.skipped}</strong> skipped</span><span><strong>{bpmResult.failed}</strong> failed</span><span><strong>{bpmResult.remaining_missing_bpm}</strong> remaining</span></div>{bpmResult.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}</section>}
      {keyResult && <section className="analysis-job-preview" aria-live="polite"><div className="settings-import-result-head"><div><h2 className="card-title">Key/Camelot analysis result</h2><p className="muted">Keyfinder wrote only eligible keys to CrateIQ’s local index.</p></div><Badge tone="succeeded">Complete</Badge></div><div className="settings-import-summary"><span><strong>{keyResult.analyzed}</strong> analyzed</span><span><strong>{keyResult.updated}</strong> updated</span><span><strong>{keyResult.skipped}</strong> skipped</span><span><strong>{keyResult.failed}</strong> failed</span><span><strong>{keyResult.remaining_missing_key}</strong> remaining</span></div>{keyResult.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}</section>}
      <section className="analysis-job-history">
        <h2 className="card-title">Analysis history</h2>
        <EmptyState title="No analysis runs yet" message={historyMessage || 'History starts when an explicit safe runner is implemented.'} />
      </section>
    </>
  )
}
