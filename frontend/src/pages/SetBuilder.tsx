import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, ChevronDown, ChevronUp, Loader2, ScrollText } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchJobLogs, fetchJob } from '../api/jobs'
import { fetchPlaylists, fetchPlaylist, runSetBuilder } from '../api/playlists'
import type {
  PlaylistDetail,
  PlaylistSummary,
  SetBuilderParams,
  Strategy,
  Structure,
  Vibe,
} from '../types/playlist'
import {
  PHASE_COLORS,
  STRATEGY_LABELS,
  STRUCTURE_LABELS,
  VIBE_LABELS,
} from '../types/playlist'
import type { Job } from '../types/job'
import PageHeader from '../components/PageHeader'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import StatusStrip from '../components/ui/StatusStrip'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtDuration(sec: number | null): string {
  if (sec == null) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function fmtMins(sec: number): string {
  return `${(sec / 60).toFixed(1)} min`
}

function fmtDate(iso: string): string {
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

// ---------------------------------------------------------------------------
// LogInline — polls job log while active; collapsible when complete
// ---------------------------------------------------------------------------

function LogInline({ jobId, active }: { jobId: string; active: boolean }) {
  const [logs,      setLogs]      = useState('')
  const [expanded, setExpanded]  = useState(active)

  // Keep expanded while job is running; collapse by default when it finishes
  useEffect(() => { if (active) setExpanded(true) }, [active])

  const load = useCallback(async () => {
    try {
      const text = await fetchJobLogs(jobId, 60)
      setLogs(text)
    } catch { /* ignore */ }
  }, [jobId])

  useEffect(() => {
    load()
    if (!active) return
    const t = setInterval(load, 2000)
    return () => clearInterval(t)
  }, [active, load])

  if (!logs) return null

  return (
    <div className="log-section">
      <button
        className="log-section-toggle"
        onClick={() => setExpanded((v) => !v)}
        type="button"
      >
        <ScrollText size={13} />
        Raw log
        {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
      </button>
      {expanded && <pre className="log-inline">{logs}</pre>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// BuildForm
// ---------------------------------------------------------------------------

interface BuildFormProps {
  onJobStarted: (job: Job) => void
}

function BuildForm({ onJobStarted }: BuildFormProps) {
  const [duration,           setDuration]           = useState(60)
  const [vibe,               setVibe]               = useState<Vibe>('peak')
  const [strategy,           setStrategy]           = useState<Strategy>('safest')
  const [structure,          setStructure]          = useState<Structure>('full')
  const [genre,              setGenre]              = useState('')
  const [maxBpmJump,         setMaxBpmJump]         = useState(3.0)
  const [strictHarmonic,     setStrictHarmonic]     = useState(true)
  const [artistRepeatWindow, setArtistRepeatWindow] = useState(3)
  const [name,               setName]               = useState('')
  const [dryRun,             setDryRun]             = useState(false)
  const [busy,               setBusy]               = useState(false)
  const [err,                setErr]                = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setErr(null)
    try {
      const params: SetBuilderParams = {
        duration,
        vibe,
        strategy,
        structure,
        genre:                genre.trim() || undefined,
        max_bpm_jump:         maxBpmJump,
        strict_harmonic:      strictHarmonic,
        artist_repeat_window: artistRepeatWindow,
        name:                 name.trim() || undefined,
        dry_run:              dryRun,
      }
      const resp = await runSetBuilder(params)
      const job  = await fetchJob(resp.job_id)
      onJobStarted(job)
    } catch (e) {
      setErr(
        e instanceof ApiError  ? e.displayMessage
        : e instanceof Error   ? e.message
        : 'Unknown error'
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="set-builder-form">
      {err && (
        <StatusStrip tone="danger" role="alert" onDismiss={() => setErr(null)}>
          <strong>Error:</strong> {err}
        </StatusStrip>
      )}

      <div className="form-grid">
        <label className="form-label">
          Duration (minutes)
          <input type="number" className="form-input" min={10} max={360}
            value={duration} onChange={(e) => setDuration(Number(e.target.value))} />
        </label>

        <label className="form-label">
          Vibe
          <select className="form-select" value={vibe} onChange={(e) => setVibe(e.target.value as Vibe)}>
            {(Object.entries(VIBE_LABELS) as [Vibe, string][]).map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        </label>

        <label className="form-label">
          Strategy
          <select className="form-select" value={strategy} onChange={(e) => setStrategy(e.target.value as Strategy)}>
            {(Object.entries(STRATEGY_LABELS) as [Strategy, string][]).map(([s, label]) => (
              <option key={s} value={s}>{label}</option>
            ))}
          </select>
        </label>

        <label className="form-label">
          Structure
          <select className="form-select" value={structure} onChange={(e) => setStructure(e.target.value as Structure)}>
            {(Object.entries(STRUCTURE_LABELS) as [Structure, string][]).map(([s, label]) => (
              <option key={s} value={s}>{label}</option>
            ))}
          </select>
        </label>

        <label className="form-label">
          Genre filter <span className="text-muted">(optional)</span>
          <input type="text" className="form-input" placeholder="e.g. afro house"
            maxLength={64} value={genre} onChange={(e) => setGenre(e.target.value)} />
        </label>

        <label className="form-label">
          Max BPM jump
          <input type="number" className="form-input" min={0} max={20} step={0.5}
            value={maxBpmJump} onChange={(e) => setMaxBpmJump(Number(e.target.value))} />
        </label>

        <label className="form-label">
          Artist repeat window
          <input type="number" className="form-input" min={0} max={10}
            value={artistRepeatWindow} onChange={(e) => setArtistRepeatWindow(Number(e.target.value))} />
        </label>

        <label className="form-label">
          Set name <span className="text-muted">(optional)</span>
          <input type="text" className="form-input" placeholder="my_set_name"
            maxLength={64} pattern="[\w\-]+" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
      </div>

      <div className="form-checkboxes">
        <label className="form-check">
          <input type="checkbox" checked={strictHarmonic} onChange={(e) => setStrictHarmonic(e.target.checked)} />
          Strict harmonic key transitions
        </label>
        <label className="form-check">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
          Dry run (preview only — no files written)
        </label>
      </div>

      <button className="btn btn--primary" onClick={submit} disabled={busy}>
        {busy
          ? <><Loader2 size={13} className="spin" /> Queuing…</>
          : 'Build Set'
        }
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// JobPanel — status + log for the most recently dispatched job
// ---------------------------------------------------------------------------

interface JobPanelProps {
  job:       Job
  onRefresh: () => void
}

function JobPanel({ job, onRefresh }: JobPanelProps) {
  const [current, setCurrent] = useState<Job>(job)
  const active = current.status === 'pending' || current.status === 'running'

  useEffect(() => {
    if (!active) return
    const t = setInterval(async () => {
      try {
        const updated = await fetchJob(job.id)
        setCurrent(updated)
        if (updated.status === 'succeeded' || updated.status === 'failed') onRefresh()
      } catch { /* ignore */ }
    }, 2000)
    return () => clearInterval(t)
  }, [active, job.id, onRefresh])

  const statusClass = current.status === 'succeeded' ? 'job-status-panel--ok'
    : current.status === 'failed' ? 'job-status-panel--error'
    : current.status === 'running' ? 'job-status-panel--running'
    : ''

  return (
    <div className={`job-status-panel ${statusClass}`}>
      <div className="job-status-panel-header">
        <span className="muted" style={{ fontSize: 12 }}>
          Job <code>{current.id.slice(0, 8)}</code>
        </span>
        <Badge tone={current.status}>{current.status}</Badge>
        {active && <span className="live-indicator">live · polling 2s</span>}
        {current.status === 'succeeded' && (
          <span className="muted" style={{ fontSize: 12 }}>Playlist saved — see list below</span>
        )}
      </div>
      <LogInline jobId={current.id} active={active} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// SetDetail — summary + track list for a selected saved set
// ---------------------------------------------------------------------------

function SetDetail({ detail, onClose }: { detail: PlaylistDetail; onClose: () => void }) {
  const { playlist, tracks } = detail
  const totalMin = tracks.reduce((s, t) => s + (t.duration_sec || 0), 0) / 60

  let cfg: Record<string, string> = {}
  try { cfg = playlist.config_json ? JSON.parse(playlist.config_json) : {} } catch { /* */ }

  return (
    <div className="set-detail">
      {/* Summary row */}
      <div className="set-detail-header">
        <div className="set-detail-title">
          <h3>{playlist.name}</h3>
          <span className="muted" style={{ fontSize: 12 }}>{fmtDate(playlist.created_at)}</span>
        </div>
        <button className="btn btn--ghost btn--xs" onClick={onClose}>Close</button>
      </div>

      <div className="set-detail-stats">
        <div className="set-stat-card">
          <span className="set-stat-value">{tracks.length}</span>
          <span className="set-stat-label">Tracks</span>
        </div>
        <div className="set-stat-card">
          <span className="set-stat-value">{totalMin.toFixed(1)}</span>
          <span className="set-stat-label">Minutes</span>
        </div>
        {cfg.vibe && (
          <div className="set-stat-card">
            <span className="set-stat-value" style={{ fontSize: 14, textTransform: 'capitalize' }}>{String(cfg.vibe)}</span>
            <span className="set-stat-label">Vibe</span>
          </div>
        )}
        {cfg.genre_filter && (
          <div className="set-stat-card">
            <span className="set-stat-value" style={{ fontSize: 14 }}>{String(cfg.genre_filter)}</span>
            <span className="set-stat-label">Genre</span>
          </div>
        )}
      </div>

      {/* Track list */}
      {tracks.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Phase</th>
                <th>Artist</th>
                <th>Title</th>
                <th>BPM</th>
                <th>Key</th>
                <th>Dur</th>
                <th>Transition</th>
              </tr>
            </thead>
            <tbody>
              {tracks.map((t) => (
                <tr key={t.position}>
                  <td className="muted td-num">{t.position}</td>
                  <td>
                    <span className="phase-badge" style={{ background: PHASE_COLORS[t.phase] ?? '#6b7280' }}>
                      {t.phase}
                    </span>
                  </td>
                  <td className="truncate-cell" title={t.artist ?? ''}>{t.artist ?? '—'}</td>
                  <td className="truncate-cell" title={t.title  ?? ''}>{t.title  ?? '—'}</td>
                  <td className="td-mono">{t.bpm != null ? t.bpm.toFixed(1) : '—'}</td>
                  <td>
                    {t.key_camelot
                      ? <Badge tone="info">{t.key_camelot}</Badge>
                      : <span className="muted">—</span>
                    }
                  </td>
                  <td className="td-mono nowrap">{fmtDuration(t.duration_sec)}</td>
                  <td className="transition-note">{t.transition_note || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SetBuilder page
// ---------------------------------------------------------------------------

export default function SetBuilder() {
  const [lastJob,       setLastJob]       = useState<Job | null>(null)
  const [playlists,     setPlaylists]     = useState<PlaylistSummary[]>([])
  const [loadingPl,     setLoadingPl]     = useState(false)
  const [selectedId,    setSelectedId]    = useState<number | null>(null)
  const [detail,        setDetail]        = useState<PlaylistDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [err,           setErr]           = useState<string | null>(null)

  const loadPlaylists = useCallback(async () => {
    setLoadingPl(true)
    try {
      setPlaylists(await fetchPlaylists())
    } catch (e) {
      setErr(e instanceof ApiError ? e.displayMessage : e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingPl(false)
    }
  }, [])

  useEffect(() => { loadPlaylists() }, [loadPlaylists])

  useEffect(() => {
    if (selectedId == null) { setDetail(null); return }
    setLoadingDetail(true)
    setDetail(null)
    fetchPlaylist(selectedId)
      .then(setDetail)
      .catch((e) => setErr(e instanceof ApiError ? e.displayMessage : String(e)))
      .finally(() => setLoadingDetail(false))
  }, [selectedId])

  function handleSelect(id: number) {
    setSelectedId((prev) => (prev === id ? null : id))
  }

  return (
    <div className="page">
      <PageHeader
        title="Set Builder"
        subtitle="Build an energy-curve DJ set from your library."
        actions={
          <button className="btn btn--ghost btn--sm" onClick={loadPlaylists} disabled={loadingPl}>
            <RefreshCw size={13} className={loadingPl ? 'spin' : ''} />
            Refresh
          </button>
        }
      />

      {err && (
        <StatusStrip tone="danger" role="alert" onDismiss={() => setErr(null)}>
          <strong>Error:</strong> {err}
        </StatusStrip>
      )}

      {/* Build form */}
      <section className="section">
        <div className="card">
          <h2 className="card-title">Build a New Set</h2>
          <BuildForm onJobStarted={(job) => { setLastJob(job); setSelectedId(null) }} />
        </div>
      </section>

      {/* Active job */}
      {lastJob && (
        <section className="section">
          <div className="card">
            <h2 className="card-title">Job Progress</h2>
            <JobPanel job={lastJob} onRefresh={loadPlaylists} />
          </div>
        </section>
      )}

      {/* Saved sets */}
      <section className="section">
        <div className="card">
          <div className="card-title-row">
            <h2 className="card-title">
              Saved Sets
              {playlists.length > 0 && <span className="card-title-count">({playlists.length})</span>}
            </h2>
          </div>

          {playlists.length === 0 ? (
            <EmptyState message="No saved sets yet. Build one above." />
          ) : (
            <div className="table-wrapper">
              <table className="table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th className="nowrap">Created</th>
                    <th>Tracks</th>
                    <th>Duration</th>
                    <th>Vibe</th>
                    <th>Genre</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {playlists.map((pl) => {
                    let vibe = '', genre = ''
                    try {
                      const cfg = pl.config_json ? JSON.parse(pl.config_json) : {}
                      vibe  = cfg.vibe         || ''
                      genre = cfg.genre_filter || ''
                    } catch { /* */ }
                    const isSelected = pl.id === selectedId
                    return (
                      <tr
                        key={pl.id}
                        className={isSelected ? 'row--selected' : 'row--clickable'}
                        onClick={() => handleSelect(pl.id)}
                      >
                        <td className="td-bold">{pl.name}</td>
                        <td className="muted nowrap">{fmtDate(pl.created_at)}</td>
                        <td>{pl.track_count}</td>
                        <td className="muted">{fmtMins(pl.duration_sec)}</td>
                        <td>
                          {vibe ? <Badge tone="info"><span style={{ textTransform: 'capitalize' }}>{vibe}</span></Badge> : <span className="muted">—</span>}
                        </td>
                        <td className="muted">{genre || '—'}</td>
                        <td>
                          <button
                            className="btn btn--ghost btn--xs"
                            onClick={(e) => { e.stopPropagation(); handleSelect(pl.id) }}
                            title="View tracks"
                          >
                            {isSelected ? 'Close' : 'View'}
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {/* Selected set detail */}
      {selectedId != null && (
        <section className="section">
          <div className="card">
            {loadingDetail && <p className="muted">Loading tracks…</p>}
            {detail && (
              <SetDetail detail={detail} onClose={() => setSelectedId(null)} />
            )}
          </div>
        </section>
      )}
    </div>
  )
}
