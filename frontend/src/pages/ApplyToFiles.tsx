import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, ArchiveRestore, Check, RotateCcw, ScanSearch, ShieldAlert } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchTracks } from '../api/tracks'
import { applyTagWritePlan, fetchTagWriteOperations, fetchTagWritePlan, restoreTagWriteFile } from '../api/tagWrite'
import type { TrackSummary } from '../types/track'
import type { TagWriteApplyResult, TagWriteOperation, TagWritePlan } from '../types/tagWrite'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'
import Badge, { type BadgeTone } from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'

const STATUS_TONE: Record<string, BadgeTone> = {
  completed: 'succeeded', restored: 'succeeded', partially_failed: 'pending',
  failed: 'failed', running: 'running', previewed: 'info',
}

function bytesLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function ApplyToFiles() {
  const [tracks, setTracks] = useState<TrackSummary[]>([])
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [plan, setPlan] = useState<TagWritePlan | null>(null)
  const [applyResult, setApplyResult] = useState<TagWriteApplyResult | null>(null)
  const [operations, setOperations] = useState<TagWriteOperation[]>([])
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState<'search' | 'plan' | 'apply' | string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const errorMessage = (e: unknown) => (e instanceof ApiError ? e.displayMessage : 'Something went wrong.')

  const loadTracks = useCallback(async (query: string) => {
    setBusy('search')
    try {
      setTracks(await fetchTracks({ search: query || undefined, limit: 50 }))
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(null) }
  }, [])

  const loadOperations = useCallback(async () => {
    try { setOperations(await fetchTagWriteOperations()) } catch { /* history is best-effort */ }
  }, [])

  useEffect(() => { void loadTracks(''); void loadOperations() }, [loadTracks, loadOperations])

  function toggle(trackId: number) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(trackId)) next.delete(trackId)
      else next.add(trackId)
      return next
    })
    setPlan(null)
    setApplyResult(null)
    setConfirm(false)
  }

  async function runPreview() {
    if (!selected.size || busy !== null) return
    setBusy('plan')
    setError(null)
    setApplyResult(null)
    setConfirm(false)
    try {
      setPlan(await fetchTagWritePlan(Array.from(selected)))
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(null) }
  }

  async function runApply() {
    if (!plan || busy !== null) return
    setBusy('apply')
    setError(null)
    try {
      const result = await applyTagWritePlan(plan.items)
      setApplyResult(result)
      setConfirm(false)
      await loadOperations()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(null) }
  }

  async function runRestore(operationId: string, trackId: number) {
    if (busy !== null) return
    setBusy(`${operationId}:${trackId}`)
    setError(null)
    try {
      await restoreTagWriteFile(operationId, trackId)
      await loadOperations()
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(null) }
  }

  const changeable = plan?.items.filter((item) => !item.blocked && item.fields.length) ?? []

  return (
    <div className="page apply-to-files-page">
      <PageHeader
        title="Apply to Files"
        subtitle="Write approved local-index metadata to actual file tags — the only step in CrateIQ that modifies your audio files."
      />
      <StatusStrip tone="danger" icon={<ShieldAlert size={15} />}>
        This writes to real files. Every write is backed up first, byte-for-byte, outside your music folder — restore is
        always available. Only artist, title, album, and genre are ever written; BPM, key, Camelot, cues, and artwork are
        never touched.
      </StatusStrip>
      {error && <StatusStrip tone="danger" role="alert" onDismiss={() => setError(null)}>{error}</StatusStrip>}

      <section className="apply-files-section">
        <h2>1. Select tracks</h2>
        <input
          className="form-input"
          placeholder="Search by artist, title, filename…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void loadTracks(search) }}
        />
        <button className="btn btn--ghost btn--sm" disabled={busy === 'search'} onClick={() => void loadTracks(search)}>
          <ScanSearch size={14} />{busy === 'search' ? 'Searching…' : 'Search'}
        </button>
        <div className="apply-files-track-list">
          {tracks.map((track) => (
            <label className="apply-files-track-row" key={track.id}>
              <input type="checkbox" checked={selected.has(track.id)} onChange={() => toggle(track.id)} />
              <span>{track.artist || 'Unknown artist'} — {track.title || track.filename}</span>
            </label>
          ))}
          {!tracks.length && <EmptyState message="No tracks found for this search." />}
        </div>
        <button className="btn btn--primary btn--sm" disabled={!selected.size || busy === 'plan'} onClick={() => void runPreview()}>
          {busy === 'plan' ? 'Building plan…' : `Preview write plan (${selected.size} selected)`}
        </button>
      </section>

      {plan && (
        <section className="apply-files-section">
          <h2>2. Review exact changes</h2>
          <p className="lib-muted">{plan.message}</p>
          <div className="apply-files-summary">
            <Badge tone="info">{plan.additions} addition{plan.additions === 1 ? '' : 's'}</Badge>
            <Badge tone="info">{plan.replacements} replacement{plan.replacements === 1 ? '' : 's'}</Badge>
            <Badge tone={plan.blocked_count ? 'failed' : 'succeeded'}>{plan.blocked_count} blocked</Badge>
            <Badge tone="pending">{plan.no_op_count} already match</Badge>
            <span className="lib-muted">Estimated backup space: {bytesLabel(plan.backup_space_estimate_bytes)}</span>
          </div>

          {plan.items.filter((item) => item.blocked).map((item) => (
            <StatusStrip key={item.track_id} tone="warn" icon={<AlertTriangle size={14} />}>
              {item.filename || `Track ${item.track_id}`}: {item.blocker}
            </StatusStrip>
          ))}

          {changeable.length > 0 && (
            <div className="apply-files-plan-scroll">
              <table className="apply-files-plan-table">
                <thead>
                  <tr><th>Track</th><th>Field</th><th>Current file value</th><th>Approved value</th><th>Action</th></tr>
                </thead>
                <tbody>
                  {changeable.map((item) => item.fields.map((field) => (
                    <tr key={`${item.track_id}-${field.field}`}>
                      <td>{item.filename}</td>
                      <td>{field.field}</td>
                      <td className="lib-muted">{field.current_file_value ?? <em>empty</em>}</td>
                      <td>{field.approved_value}</td>
                      <td><Badge tone={field.action === 'ADD' ? 'succeeded' : 'pending'}>{field.action}</Badge></td>
                    </tr>
                  )))}
                </tbody>
              </table>
            </div>
          )}

          {changeable.length > 0 && (
            <div className="apply-files-confirm">
              <label className="form-check">
                <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />
                I have reviewed the exact changes above. A byte-for-byte backup of every file will be created before any write.
              </label>
              <button className="btn btn--danger btn--sm" disabled={!confirm || busy === 'apply'} onClick={() => void runApply()}>
                <Check size={13} />{busy === 'apply' ? 'Backing up and writing…' : 'Backup, write, and verify'}
              </button>
            </div>
          )}
        </section>
      )}

      {applyResult && (
        <section className="apply-files-section">
          <h2>3. Result</h2>
          <StatusStrip tone={applyResult.status === 'completed' ? 'good' : applyResult.status === 'failed' ? 'danger' : 'warn'}>
            Operation {applyResult.operation_id.slice(0, 8)}: {applyResult.applied} applied, {applyResult.skipped} skipped, {applyResult.failed} failed.
          </StatusStrip>
          {applyResult.results.filter((r) => r.status !== 'applied').map((r) => (
            <p className="lib-muted" key={r.track_id}>Track {r.track_id}: {r.reason}</p>
          ))}
        </section>
      )}

      <section className="apply-files-section">
        <h2>Operations history</h2>
        {!operations.length ? (
          <EmptyState message="No write-back operations yet." />
        ) : (
          <div className="apply-files-history">
            {operations.map((op) => (
              <div className="apply-files-history-row" key={op.id}>
                <div>
                  <strong>{op.id.slice(0, 8)}</strong>
                  <Badge tone={STATUS_TONE[op.status] ?? 'info'}>{op.status.replace('_', ' ')}</Badge>
                  <span className="lib-muted">{new Date(op.created_at).toLocaleString()}</span>
                </div>
                <span className="lib-muted">{op.applied_count} applied · {op.skipped_count} skipped · {op.failed_count} failed</span>
                {op.status === 'completed' || op.status === 'partially_failed' ? (
                  <div className="apply-files-restore-list">
                    {op.results.filter((r) => r.status === 'applied' && !r.restored).map((r) => (
                      <button
                        key={r.track_id}
                        className="btn btn--ghost btn--xs"
                        disabled={busy === `${op.id}:${r.track_id}`}
                        onClick={() => void runRestore(op.id, r.track_id)}
                      >
                        <RotateCcw size={12} />
                        {busy === `${op.id}:${r.track_id}` ? 'Restoring…' : `Restore track ${r.track_id}`}
                      </button>
                    ))}
                    {op.results.some((r) => r.restored) && (
                      <span className="lib-muted"><ArchiveRestore size={12} /> {op.results.filter((r) => r.restored).length} restored</span>
                    )}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
