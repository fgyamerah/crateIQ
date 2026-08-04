import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Loader2 } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchJobLogs, fetchJob } from '../api/jobs'
import { validateExport, runExport, fetchExports, exportCrate, exportRekordbox, exportSerato, fetchExportableCrates, previewCrateExport, previewRekordboxExport, previewSeratoExport } from '../api/exports'
import type {
  ExcludedTrack,
  ExclusionCategory,
  ExportRunRequest,
  ExportWarning,
  ValidateResponse,
  ValidationStats,
  WarningLevel,
  CrateExportFormat,
  CrateExportPreview,
  CrateExportRequest,
  CratePathMode,
  RekordboxExportPreview,
  RekordboxExportRequest,
  SeratoExportPreview,
  SeratoExportRequest,
} from '../types/export'
import type { CrateSummary } from '../types/crate'
import {
  CATEGORY_COLORS,
  CATEGORY_LABELS,
} from '../types/export'
import type { Job } from '../types/job'
import ErrorBanner from '../components/ErrorBanner'
import PageHeader from '../components/PageHeader'
import StatusStrip, { type StatusStripTone } from '../components/ui/StatusStrip'
import EmptyState from '../components/ui/EmptyState'
import Badge from '../components/ui/Badge'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtDate(iso: string): string {
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

function pct(n: number, total: number): string {
  if (!total) return '0%'
  return `${((n / total) * 100).toFixed(1)}%`
}

// ---------------------------------------------------------------------------
// Warning banner row
// ---------------------------------------------------------------------------

const WARNING_TONE: Record<WarningLevel, StatusStripTone> = {
  info: 'info',
  warning: 'warn',
  error: 'danger',
}

function WarningItem({ w }: { w: ExportWarning }) {
  return (
    <StatusStrip tone={WARNING_TONE[w.level]} role={w.level === 'error' ? 'alert' : 'status'}>
      <span className="export-warning-level">{w.level.toUpperCase()}</span>
      <span>{w.message}</span>
    </StatusStrip>
  )
}

// ---------------------------------------------------------------------------
// Stats summary grid
// ---------------------------------------------------------------------------

function StatBox({ label, value, sub, color }: { label: string; value: number; sub?: string; color?: string }) {
  return (
    <div className="export-stat-box">
      <div className="export-stat-value" style={color ? { color } : undefined}>{value.toLocaleString()}</div>
      <div className="export-stat-label">{label}</div>
      {sub && <div className="export-stat-sub">{sub}</div>}
    </div>
  )
}

function ValidationSummary({ stats }: { stats: ValidationStats }) {
  const { total_scanned, valid_count, invalid_count } = stats
  return (
    <div className="export-stat-grid">
      <StatBox label="Scanned"  value={total_scanned} />
      <StatBox
        label="Valid"
        value={valid_count}
        sub={pct(valid_count, total_scanned)}
        color="#22c55e"
      />
      <StatBox
        label="Excluded"
        value={invalid_count}
        sub={pct(invalid_count, total_scanned)}
        color={invalid_count > 0 ? '#ef4444' : undefined}
      />
      {stats.missing_analysis > 0 && (
        <StatBox label="Missing Analysis" value={stats.missing_analysis} color="#f59e0b" />
      )}
      {stats.missing_metadata > 0 && (
        <StatBox label="Missing Metadata" value={stats.missing_metadata} color="#8b5cf6" />
      )}
      {stats.stale_db > 0 && (
        <StatBox label="Stale Paths" value={stats.stale_db} color="#ef4444" />
      )}
      {stats.junk > 0 && (
        <StatBox label="Junk Files" value={stats.junk} color="#6b7280" />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Excluded tracks table
// ---------------------------------------------------------------------------

const CATEGORIES: ExclusionCategory[] = [
  'MISSING_ANALYSIS',
  'MISSING_METADATA',
  'STALE_DB',
  'JUNK_PLACEHOLDER',
  'BAD_PATH',
  'OTHER',
]

const PAGE_SIZE = 50

interface ExcludedTableProps {
  excluded:  ExcludedTrack[]
  truncated: boolean
}

function ExcludedTable({ excluded, truncated }: ExcludedTableProps) {
  const [filter,    setFilter]   = useState<ExclusionCategory | 'ALL'>('ALL')
  const [search,    setSearch]   = useState('')
  const [page,      setPage]     = useState(0)

  const filtered = excluded.filter((t) => {
    if (filter !== 'ALL' && t.category !== filter) return false
    if (search) {
      const q = search.toLowerCase()
      return (
        t.filename.toLowerCase().includes(q) ||
        (t.artist  ?? '').toLowerCase().includes(q) ||
        (t.title   ?? '').toLowerCase().includes(q)
      )
    }
    return true
  })

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const safePage   = Math.min(page, totalPages - 1)
  const slice      = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE)

  // Reset page when filter/search changes
  useEffect(() => { setPage(0) }, [filter, search])

  // Count per category for filter tabs
  const catCounts: Record<string, number> = {}
  for (const t of excluded) {
    catCounts[t.category] = (catCounts[t.category] ?? 0) + 1
  }

  return (
    <div className="excluded-panel">
      {/* Filter tabs */}
      <div className="export-filter-bar">
        <button
          className={`export-filter-btn ${filter === 'ALL' ? 'export-filter-btn--active' : ''}`}
          onClick={() => setFilter('ALL')}
        >
          All ({excluded.length}{truncated ? '+' : ''})
        </button>
        {CATEGORIES.filter((c) => catCounts[c] > 0).map((c) => (
          <button
            key={c}
            className={`export-filter-btn ${filter === c ? 'export-filter-btn--active' : ''}`}
            style={filter === c ? { borderColor: CATEGORY_COLORS[c] } : undefined}
            onClick={() => setFilter(c)}
          >
            <span
              className="export-cat-dot"
              style={{ background: CATEGORY_COLORS[c] }}
            />
            {CATEGORY_LABELS[c]} ({catCounts[c]})
          </button>
        ))}
      </div>

      {/* Search */}
      <div style={{ marginBottom: 10 }}>
        <input
          type="search"
          className="form-input"
          placeholder="Search filename, artist, title…"
          style={{ maxWidth: 340 }}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {truncated && filter === 'ALL' && !search && (
        <p className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
          Showing first {excluded.length} excluded tracks — run the export job to see the full log.
        </p>
      )}

      {filtered.length === 0 ? (
        <p className="muted">No tracks match this filter.</p>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Category</th>
                  <th>Filename</th>
                  <th>Artist</th>
                  <th>Title</th>
                  <th>BPM</th>
                  <th>Key</th>
                  <th>Reasons</th>
                </tr>
              </thead>
              <tbody>
                {slice.map((t, i) => (
                  <tr key={i}>
                    <td>
                      <span
                        className="exclusion-cat-badge"
                        style={{ background: CATEGORY_COLORS[t.category] }}
                      >
                        {CATEGORY_LABELS[t.category]}
                      </span>
                    </td>
                    <td className="truncate-cell" title={t.filepath}>{t.filename}</td>
                    <td className="truncate-cell">{t.artist ?? <span className="muted">—</span>}</td>
                    <td className="truncate-cell">{t.title  ?? <span className="muted">—</span>}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {t.bpm != null ? t.bpm.toFixed(1) : <span className="muted">—</span>}
                    </td>
                    <td>
                      {t.key_camelot
                        ? <span className="badge badge--info">{t.key_camelot}</span>
                        : <span className="muted">—</span>}
                    </td>
                    <td className="reasons-cell">
                      {t.reasons.map((r, ri) => (
                        <div key={ri} className="reason-line">
                          {r.replace(/^\[[A-Z_]+\]\s*/, '')}
                        </div>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="pagination">
              <button
                className="btn btn--ghost btn--sm"
                disabled={safePage === 0}
                onClick={() => setPage(safePage - 1)}
              >
                ← Prev
              </button>
              <span className="muted">
                {safePage + 1} / {totalPages} ({filtered.length} tracks)
              </span>
              <button
                className="btn btn--ghost btn--sm"
                disabled={safePage >= totalPages - 1}
                onClick={() => setPage(safePage + 1)}
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Output paths info
// ---------------------------------------------------------------------------

function OutputPaths({ paths }: { paths: Record<string, string> }) {
  if (!Object.keys(paths).length) return null
  return (
    <div className="output-paths">
      {Object.entries(paths).map(([key, val]) => (
        <div key={key} className="output-path-row">
          <span className="output-path-key">{key.toUpperCase()}</span>
          <code className="output-path-val">{val}</code>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Inline log viewer (same as BpmReview / SetBuilder)
// ---------------------------------------------------------------------------

function LogInline({ jobId, active }: { jobId: string; active: boolean }) {
  const [logs, setLogs] = useState('')
  const load = useCallback(async () => {
    try { setLogs(await fetchJobLogs(jobId, 60)) } catch { /* ignore */ }
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
// Run form + active job panel
// ---------------------------------------------------------------------------

interface RunPanelProps {
  canRun:     boolean
  onJobStart: (job: Job) => void
}

function RunPanel({ canRun, onJobStart }: RunPanelProps) {
  const [dryRun,          setDryRun]         = useState(false)
  const [skipM3u,         setSkipM3u]        = useState(false)
  const [forceXml,        setForceXml]       = useState(false)
  const [recoverMissing,  setRecoverMissing] = useState(false)
  const [busy,            setBusy]           = useState(false)
  const [err,             setErr]            = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setErr(null)
    try {
      const req: ExportRunRequest = {
        dry_run:         dryRun,
        skip_m3u:        skipM3u,
        force_xml:       forceXml,
        recover_missing: recoverMissing,
      }
      const resp = await runExport(req)
      const job  = await fetchJob(resp.job_id)
      onJobStart(job)
    } catch (e) {
      setErr(e instanceof ApiError ? e.displayMessage : e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="run-panel">
      {err && <ErrorBanner message={err} onDismiss={() => setErr(null)} />}

      <div className="form-checkboxes" style={{ marginBottom: 14 }}>
        <label className="form-check">
          <input type="checkbox" checked={dryRun}         onChange={(e) => setDryRun(e.target.checked)} />
          Dry run (preview only)
        </label>
        <label className="form-check">
          <input type="checkbox" checked={skipM3u}        onChange={(e) => setSkipM3u(e.target.checked)} />
          Skip M3U generation
        </label>
        <label className="form-check">
          <input type="checkbox" checked={recoverMissing} onChange={(e) => setRecoverMissing(e.target.checked)} />
          Recover missing analysis (slower)
        </label>
        <label className="form-check" title="NOT recommended when using Mixed In Key">
          <input type="checkbox" checked={forceXml}       onChange={(e) => setForceXml(e.target.checked)} />
          Force XML export{' '}
          <span className="muted" style={{ fontSize: 11 }}>(not recommended with MIK)</span>
        </label>
      </div>

      <button
        className="btn btn--primary"
        onClick={submit}
        disabled={busy || !canRun}
      >
        {busy
          ? <><Loader2 size={13} className="spin" /> Queuing…</>
          : dryRun ? 'Run Dry Export' : 'Run Export'
        }
      </button>
    </div>
  )
}

interface ActiveJobPanelProps {
  job:       Job
  onRefresh: () => void
}

function ActiveJobPanel({ job, onRefresh }: ActiveJobPanelProps) {
  const [current, setCurrent] = useState<Job>(job)
  const active = current.status === 'pending' || current.status === 'running'

  useEffect(() => {
    if (!active) return
    const t = setInterval(async () => {
      try {
        const updated = await fetchJob(job.id)
        setCurrent(updated)
        if (updated.status === 'succeeded' || updated.status === 'failed') {
          onRefresh()
        }
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(t)
  }, [active, job.id, onRefresh])

  return (
    <div className="bpm-job-panel">
      <div className="bpm-job-header">
        <span className="muted" style={{ fontSize: 12 }}>
          Job <code>{current.id.slice(0, 8)}</code>
        </span>
        <span className={`badge badge--${current.status}`}>{current.status}</span>
        {active && <span className="live-indicator">live · polling 2s</span>}
        {current.status === 'succeeded' && (
          <span className="muted" style={{ fontSize: 12 }}>Export complete</span>
        )}
        {current.status === 'failed' && (
          <span style={{ color: '#ef4444', fontSize: 12 }}>Export failed — see log below</span>
        )}
      </div>
      <LogInline jobId={current.id} active={active} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Past exports table
// ---------------------------------------------------------------------------

function PastExports({ jobs }: { jobs: Job[] }) {
  if (!jobs.length) {
    return <p className="muted empty-hint">No past exports.</p>
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Job ID</th>
            <th>Status</th>
            <th>Started</th>
            <th>Finished</th>
            <th>Args</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id}>
              <td><code style={{ fontSize: 11 }}>{j.id.slice(0, 12)}…</code></td>
              <td><span className={`badge badge--${j.status}`}>{j.status}</span></td>
              <td className="muted">{j.started_at  ? fmtDate(j.started_at)  : '—'}</td>
              <td className="muted">{j.finished_at ? fmtDate(j.finished_at) : '—'}</td>
              <td className="muted" style={{ fontSize: 11 }}>{j.args.join(' ') || '(defaults)'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Export page
// ---------------------------------------------------------------------------

function CrateExportPanel() {
  const [crates, setCrates] = useState<CrateSummary[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [request, setRequest] = useState<CrateExportRequest>({ format: 'm3u8', path_mode: 'filename', include_metadata: true, line_endings: 'lf' })
  const [preview, setPreview] = useState<CrateExportPreview | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const load = () => fetchExportableCrates().then((items) => { setCrates(items); setSelected((value) => value ?? items[0]?.id ?? null) }).catch((e) => setError(e instanceof ApiError ? e.displayMessage : 'Could not load Manual Crates.'))
  useEffect(() => { load() }, [])
  const makePreview = async () => { if (!selected) return; setBusy(true); setError(null); setResult(null); try { setPreview(await previewCrateExport(selected, request)) } catch (e) { setError(e instanceof ApiError ? e.displayMessage : 'Could not preview crate export.') } finally { setBusy(false) } }
  const write = async () => { if (!selected) return; setBusy(true); setError(null); try { const output = await exportCrate(selected, request); setResult(output.output_path); setPreview(output) } catch (e) { setError(e instanceof ApiError ? e.displayMessage : 'Could not create crate export.') } finally { setBusy(false) } }
  return <section className="section"><div className="card crate-export-panel"><div className="card-title-row"><div><h2 className="card-title">Manual Crate Exports</h2><p className="muted">Portable files only — never writes music files, tags, or DJ app databases.</p></div><Badge tone="info">Safe local export</Badge></div>{error && <StatusStrip tone="danger" role="alert" onDismiss={() => setError(null)}>{error}</StatusStrip>}{result && <StatusStrip tone="good">Created export: <code>{result}</code></StatusStrip>}{crates.length === 0 ? <EmptyState title="No Manual Crates" message="Create or save a crate before exporting a portable playlist." /> : <><div className="crate-export-controls"><label>Crate<select className="form-input" value={selected ?? ''} onChange={(e) => { setSelected(Number(e.target.value)); setPreview(null); setResult(null) }}>{crates.map((crate) => <option key={crate.id} value={crate.id}>{crate.name} ({crate.track_count})</option>)}</select></label><label>Format<select className="form-input" value={request.format} onChange={(e) => setRequest({ ...request, format: e.target.value as CrateExportFormat })}><option value="csv">CSV</option><option value="json">JSON</option><option value="m3u">M3U</option><option value="m3u8">M3U8 (UTF-8)</option></select></label><label>Path mode<select className="form-input" value={request.path_mode} onChange={(e) => setRequest({ ...request, path_mode: e.target.value as CratePathMode })}><option value="filename">Filename (safe default)</option><option value="relative">Relative to library root</option><option value="absolute">Absolute path</option></select></label><label>Line endings<select className="form-input" value={request.line_endings} onChange={(e) => setRequest({ ...request, line_endings: e.target.value as 'lf' | 'crlf' })}><option value="lf">LF</option><option value="crlf">CRLF</option></select></label></div><div className="form-checkboxes"><label className="form-check"><input type="checkbox" checked={request.include_metadata} onChange={(e) => setRequest({ ...request, include_metadata: e.target.checked })} /> Include metadata</label></div><div className="crate-export-actions"><button className="btn btn--ghost" disabled={busy} onClick={() => void makePreview()}>{busy ? 'Working…' : 'Preview content'}</button><button className="btn btn--primary" disabled={busy} onClick={() => void write()}>{busy ? 'Writing…' : 'Create export file'}</button></div>{preview && <><div className="crate-export-summary"><Badge tone="info">{preview.track_count} tracks</Badge><span>{preview.format.toUpperCase()} · {preview.path_mode} paths</span></div>{preview.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}<pre className="crate-export-preview">{preview.content}</pre></>}</>}</div></section>
}

function SeratoExportPanel() {
  const [crates, setCrates] = useState<CrateSummary[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [pathMode, setPathMode] = useState<CratePathMode>('filename')
  const [dryRun, setDryRun] = useState(true)
  const [preview, setPreview] = useState<SeratoExportPreview | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchExportableCrates()
      .then((items) => {
        setCrates(items)
        setSelected((value) => value ?? items[0]?.id ?? null)
      })
      .catch((e) => setError(e instanceof ApiError ? e.displayMessage : 'Could not load Manual Crates.'))
  }, [])

  const makePreview = async () => {
    if (!selected) return
    setBusy(true); setError(null); setResult(null)
    try { setPreview(await previewSeratoExport(selected, pathMode)) }
    catch (e) { setError(e instanceof ApiError ? e.displayMessage : 'Could not preview the staged Serato export.') }
    finally { setBusy(false) }
  }

  const write = async () => {
    if (!selected) return
    setBusy(true); setError(null)
    const request: SeratoExportRequest = { path_mode: pathMode, destination_mode: 'staged', backup_existing: true, dry_run: dryRun }
    try {
      const output = await exportSerato(selected, request)
      setPreview(output)
      setResult(output.written ? output.staged_directory : null)
    } catch (e) { setError(e instanceof ApiError ? e.displayMessage : 'Could not create the staged Serato export.') }
    finally { setBusy(false) }
  }

  return (
    <section className="section">
      <div className="card crate-export-panel">
        <div className="card-title-row">
          <div>
            <h2 className="card-title">Serato Staged Export</h2>
            <p className="muted">Preview-first, backup-first handoff for a Manual Crate. It never writes to your live Serato library.</p>
          </div>
          <Badge tone="info">Staged only</Badge>
        </div>
        <StatusStrip tone="info">Creates an M3U8 and manifest under the local export folder. Exact Serato <code>.crate</code> binary writing is deferred.</StatusStrip>
        {error && <StatusStrip tone="danger" role="alert" onDismiss={() => setError(null)}>{error}</StatusStrip>}
        {result && <StatusStrip tone="good">Created staged export: <code>{result}</code></StatusStrip>}
        {crates.length === 0 ? <EmptyState title="No Manual Crates" message="Create or save a Manual Crate before staging a Serato handoff." /> : <>
          <div className="crate-export-controls serato-export-controls">
            <label>Crate<select className="form-input" value={selected ?? ''} onChange={(e) => { setSelected(Number(e.target.value)); setPreview(null); setResult(null) }}>{crates.map((crate) => <option key={crate.id} value={crate.id}>{crate.name} ({crate.track_count})</option>)}</select></label>
            <label>Path mode<select className="form-input" value={pathMode} onChange={(e) => setPathMode(e.target.value as CratePathMode)}><option value="filename">Filename (safe default)</option><option value="relative">Relative to library root</option><option value="absolute">Absolute path</option></select></label>
          </div>
          <label className="form-check"><input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} /> Dry run only (do not create files)</label>
          <p className="muted serato-export-note">Custom and live Serato destinations are deliberately unavailable. Each staged export gets a new folder, so existing files are never replaced.</p>
          <div className="crate-export-actions"><button className="btn btn--ghost" disabled={busy} onClick={() => void makePreview()}>{busy ? 'Working…' : 'Preview staged export'}</button><button className="btn btn--primary" disabled={busy} onClick={() => void write()}>{busy ? 'Working…' : dryRun ? 'Run dry preview' : 'Create staged export'}</button></div>
          {preview && <><div className="crate-export-summary"><Badge tone="info">{preview.track_count} tracks</Badge><span>Staged M3U8 · {pathMode} paths</span></div><div className="serato-export-paths"><span>Folder: <code>{preview.staged_directory}</code></span><span>M3U8: <code>{preview.m3u8_path}</code></span></div>{preview.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}<pre className="crate-export-preview">{preview.m3u8_content}</pre></>}
        </>}
      </div>
    </section>
  )
}

function RekordboxExportPanel() {
  const [crates, setCrates] = useState<CrateSummary[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [pathMode, setPathMode] = useState<CratePathMode>('filename')
  const [includeMetadata, setIncludeMetadata] = useState(true)
  const [dryRun, setDryRun] = useState(true)
  const [preview, setPreview] = useState<RekordboxExportPreview | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchExportableCrates()
      .then((items) => {
        setCrates(items)
        setSelected((value) => value ?? items[0]?.id ?? null)
      })
      .catch((e) => setError(e instanceof ApiError ? e.displayMessage : 'Could not load Manual Crates.'))
  }, [])

  const makePreview = async () => {
    if (!selected) return
    setBusy(true); setError(null); setResult(null)
    try { setPreview(await previewRekordboxExport(selected, { path_mode: pathMode, include_metadata: includeMetadata })) }
    catch (e) { setError(e instanceof ApiError ? e.displayMessage : 'Could not preview the staged Rekordbox XML export.') }
    finally { setBusy(false) }
  }

  const write = async () => {
    if (!selected) return
    setBusy(true); setError(null)
    const request: RekordboxExportRequest = { path_mode: pathMode, destination_mode: 'staged', include_metadata: includeMetadata, dry_run: dryRun }
    try {
      const output = await exportRekordbox(selected, request)
      setPreview(output)
      setResult(output.written ? output.output_path : null)
    } catch (e) { setError(e instanceof ApiError ? e.displayMessage : 'Could not create the staged Rekordbox XML export.') }
    finally { setBusy(false) }
  }

  return (
    <section className="section">
      <div className="card crate-export-panel">
        <div className="card-title-row">
          <div>
            <h2 className="card-title">Rekordbox XML Export</h2>
            <p className="muted">Creates an importable XML file for a Manual Crate without writing to your live Rekordbox database.</p>
          </div>
          <Badge tone="info">Staged XML</Badge>
        </div>
        <StatusStrip tone="info">This staged Rekordbox XML export does not modify files, tags, BPM, key, cue points, or Mixed In Key data.</StatusStrip>
        {error && <StatusStrip tone="danger" role="alert" onDismiss={() => setError(null)}>{error}</StatusStrip>}
        {result && <StatusStrip tone="good">Created staged XML: <code>{result}</code></StatusStrip>}
        {crates.length === 0 ? <EmptyState title="No Manual Crates" message="Create or save a Manual Crate before staging a Rekordbox XML import." /> : <>
          <div className="crate-export-controls rekordbox-export-controls">
            <label>Crate<select className="form-input" value={selected ?? ''} onChange={(e) => { setSelected(Number(e.target.value)); setPreview(null); setResult(null) }}>{crates.map((crate) => <option key={crate.id} value={crate.id}>{crate.name} ({crate.track_count})</option>)}</select></label>
            <label>Path mode<select className="form-input" value={pathMode} onChange={(e) => setPathMode(e.target.value as CratePathMode)}><option value="filename">Filename (safe default)</option><option value="relative">Relative to library root</option><option value="absolute">Absolute path</option></select></label>
          </div>
          <div className="form-checkboxes"><label className="form-check"><input type="checkbox" checked={includeMetadata} onChange={(e) => setIncludeMetadata(e.target.checked)} /> Include metadata</label><label className="form-check"><input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} /> Dry run only (do not create files)</label></div>
          <p className="muted serato-export-note">The file is staged below the local export directory for manual import. Custom, live Rekordbox, USB, and device destinations are deliberately unavailable.</p>
          <div className="crate-export-actions"><button className="btn btn--ghost" disabled={busy} onClick={() => void makePreview()}>{busy ? 'Working…' : 'Preview XML'}</button><button className="btn btn--primary" disabled={busy} onClick={() => void write()}>{busy ? 'Working…' : dryRun ? 'Run dry preview' : 'Create staged XML'}</button></div>
          {preview && <><div className="crate-export-summary"><Badge tone="info">{preview.track_count} tracks</Badge><span>Rekordbox XML · {pathMode} paths</span></div><div className="serato-export-paths"><span>Output: <code>{preview.output_path}</code></span></div>{preview.safety_notes.map((note) => <StatusStrip key={note} tone="info">{note}</StatusStrip>)}{preview.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}<pre className="crate-export-preview">{preview.xml_content}</pre></>}
        </>}
      </div>
    </section>
  )
}

export default function Export() {
  const [validation,    setValidation]   = useState<ValidateResponse | null>(null)
  const [validating,    setValidating]   = useState(false)
  const [validateErr,   setValidateErr]  = useState<string | null>(null)

  const [lastJob,       setLastJob]      = useState<Job | null>(null)
  const [pastJobs,      setPastJobs]     = useState<Job[]>([])
  const [loadingJobs,   setLoadingJobs]  = useState(false)

  const loadPastJobs = useCallback(async () => {
    setLoadingJobs(true)
    try { setPastJobs(await fetchExports()) } catch { /* ignore */ }
    finally { setLoadingJobs(false) }
  }, [])

  useEffect(() => { loadPastJobs() }, [loadPastJobs])

  async function handleValidate() {
    setValidating(true)
    setValidateErr(null)
    try {
      setValidation(await validateExport())
    } catch (e) {
      setValidateErr(e instanceof ApiError ? e.displayMessage : e instanceof Error ? e.message : String(e))
    } finally {
      setValidating(false)
    }
  }

  const canRun = !validating

  return (
    <div className="page">
      <PageHeader
        title="Export"
        subtitle="Validate your library and export M3U playlists for Rekordbox. Run validation first to see which tracks will be excluded."
        actions={
          <button className="btn btn--ghost btn--sm" onClick={loadPastJobs} disabled={loadingJobs}>
            <RefreshCw size={13} className={loadingJobs ? 'spin' : ''} />
            Refresh
          </button>
        }
      />

      <CrateExportPanel />
      <SeratoExportPanel />
      <RekordboxExportPanel />

      {/* ------------------------------------------------------------------ */}
      {/* Validation section                                                  */}
      {/* ------------------------------------------------------------------ */}
      <section className="section">
        <div className="card">
          <div className="card-title-row">
            <h2 className="card-title">Pre-Export Validation</h2>
            <button
              className="btn btn--primary btn--sm"
              onClick={handleValidate}
              disabled={validating}
            >
              {validating ? 'Validating…' : validation ? 'Re-validate' : 'Validate Library'}
            </button>
          </div>

          {validateErr && (
            <ErrorBanner message={validateErr} onDismiss={() => setValidateErr(null)} />
          )}

          {validation && (
            <>
              {/* Warnings */}
              {validation.warnings.length > 0 && (
                <div className="export-warnings-list">
                  {validation.warnings.map((w, i) => (
                    <WarningItem key={i} w={w} />
                  ))}
                </div>
              )}

              {/* Stats */}
              <ValidationSummary stats={validation.stats} />

              {/* Output paths */}
              <div className="output-paths-section">
                <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                  Output locations:
                </div>
                <OutputPaths paths={validation.output_paths} />
              </div>
            </>
          )}

          {!validation && !validating && (
            <p className="muted empty-hint">
              Click Validate Library to check which tracks are ready for export.
            </p>
          )}
        </div>
      </section>

      {/* ------------------------------------------------------------------ */}
      {/* Excluded tracks                                                     */}
      {/* ------------------------------------------------------------------ */}
      {validation && validation.excluded.length > 0 && (
        <section className="section">
          <div className="card">
            <h2 className="card-title">
              Excluded Tracks ({validation.stats.invalid_count}
              {validation.truncated ? '+' : ''})
            </h2>
            <ExcludedTable
              excluded={validation.excluded}
              truncated={validation.truncated}
            />
          </div>
        </section>
      )}

      {validation && validation.excluded.length === 0 && validation.stats.total_scanned > 0 && (
        <section className="section">
          <div className="card">
            <p style={{ color: '#22c55e' }}>
              ✓ All {validation.stats.valid_count} tracks pass validation — ready to export.
            </p>
          </div>
        </section>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Run export                                                          */}
      {/* ------------------------------------------------------------------ */}
      <section className="section">
        <div className="card">
          <h2 className="card-title">Run Export</h2>
          <RunPanel canRun={canRun} onJobStart={(job) => { setLastJob(job); loadPastJobs() }} />
        </div>
      </section>

      {/* Active job */}
      {lastJob && (
        <section className="section">
          <div className="card">
            <h2 className="card-title">Export Job</h2>
            <ActiveJobPanel job={lastJob} onRefresh={loadPastJobs} />
          </div>
        </section>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Export history                                                      */}
      {/* ------------------------------------------------------------------ */}
      <section className="section">
        <div className="card">
          <div className="card-title-row">
            <h2 className="card-title">Export History</h2>
            <button
              className="btn btn--ghost btn--sm"
              onClick={loadPastJobs}
              disabled={loadingJobs}
            >
              {loadingJobs ? 'Loading…' : 'Refresh'}
            </button>
          </div>
          <PastExports jobs={pastJobs} />
        </div>
      </section>
    </div>
  )
}
