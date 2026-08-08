import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { FolderInput, Inbox as InboxIcon, Loader2, RefreshCw, ShieldCheck, Sparkles, Upload, Wand2 } from 'lucide-react'
import { ApiError } from '../api/client'
import {
  applyPromotion,
  cancelPrepareOperation,
  cleanSelected,
  enrichSelected,
  fetchInboxTracks,
  fetchPreparePreview,
  fetchPrepareOperation,
  fetchWorkspaceStatus,
  importToInbox,
  previewPromotion,
  startProcessAll,
} from '../api/workspace'
import type {
  InboxTrackPage, PreparationOperation, PreparePreflight,
  PromotionPreview, WorkspaceImportResult, WorkspaceStatus,
} from '../api/workspace'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'

function messageFor(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.displayMessage : fallback
}

const POLL_INTERVAL_MS = 1500

export default function Inbox() {
  const [status, setStatus] = useState<WorkspaceStatus | null>(null)
  const [tracks, setTracks] = useState<InboxTrackPage | null>(null)
  const [preview, setPreview] = useState<PromotionPreview | null>(null)
  const [preflight, setPreflight] = useState<PreparePreflight | null>(null)
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [promoting, setPromoting] = useState(false)
  const [importPaths, setImportPaths] = useState('')
  const [importResult, setImportResult] = useState<WorkspaceImportResult | null>(null)
  const [confirmingPromotion, setConfirmingPromotion] = useState(false)
  const [confirmingProcessAll, setConfirmingProcessAll] = useState(false)
  const [operation, setOperation] = useState<PreparationOperation | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [batchBusy, setBatchBusy] = useState<'clean' | 'enrich' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const nextStatus = await fetchWorkspaceStatus()
      setStatus(nextStatus)
      if (nextStatus.state === 'managed_workspace') {
        const [nextTracks, nextPreview, nextPreflight] = await Promise.all([
          fetchInboxTracks({ limit: 200 }),
          previewPromotion(),
          fetchPreparePreview(),
        ])
        setTracks(nextTracks)
        setPreview(nextPreview)
        setPreflight(nextPreflight)
      } else {
        setTracks(null)
        setPreview(null)
        setPreflight(null)
      }
    } catch (err) {
      setError(messageFor(err, 'Could not load the managed workspace.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => () => { if (pollRef.current) clearTimeout(pollRef.current) }, [])

  const pollOperation = useCallback((operationId: string) => {
    const tick = async () => {
      try {
        const next = await fetchPrepareOperation(operationId)
        setOperation(next)
        if (next.status === 'running') {
          pollRef.current = setTimeout(tick, POLL_INTERVAL_MS)
        } else {
          await load()
        }
      } catch (err) {
        setError(messageFor(err, 'Lost track of the Process All operation.'))
      }
    }
    void tick()
  }, [load])

  const doImport = async () => {
    const paths = importPaths.split('\n').map((line) => line.trim()).filter(Boolean)
    if (!paths.length) return
    setImporting(true)
    setError(null)
    setImportResult(null)
    try {
      const result = await importToInbox(paths)
      setImportResult(result)
      setImportPaths('')
      await load()
    } catch (err) {
      setError(messageFor(err, 'Import failed.'))
    } finally {
      setImporting(false)
    }
  }

  const doPromote = async () => {
    if (!preview) return
    const readyIds = preview.items.filter((item) => item.ready).map((item) => item.track_id)
    if (!readyIds.length) return
    setPromoting(true)
    setError(null)
    try {
      await applyPromotion(readyIds)
      setConfirmingPromotion(false)
      await load()
    } catch (err) {
      setError(messageFor(err, 'Promotion failed.'))
    } finally {
      setPromoting(false)
    }
  }

  const doProcessAll = async () => {
    setError(null)
    try {
      const { operation_id } = await startProcessAll()
      setConfirmingProcessAll(false)
      pollOperation(operation_id)
    } catch (err) {
      setError(messageFor(err, 'Process All failed to start.'))
    }
  }

  const doCancelProcessAll = async () => {
    if (!operation) return
    try {
      await cancelPrepareOperation(operation.id)
    } catch (err) {
      setError(messageFor(err, 'Could not request cancellation.'))
    }
  }

  const toggleSelected = (trackId: number) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(trackId)) next.delete(trackId)
      else next.add(trackId)
      return next
    })
  }

  const doCleanSelected = async () => {
    if (!selected.size) return
    setBatchBusy('clean')
    setError(null)
    try {
      await cleanSelected(Array.from(selected))
      await load()
    } catch (err) {
      setError(messageFor(err, 'Clean Selected failed.'))
    } finally {
      setBatchBusy(null)
    }
  }

  const doEnrichSelected = async () => {
    if (!selected.size) return
    setBatchBusy('enrich')
    setError(null)
    try {
      await enrichSelected(Array.from(selected))
      await load()
    } catch (err) {
      setError(messageFor(err, 'Enrich Selected failed.'))
    } finally {
      setBatchBusy(null)
    }
  }

  const readinessByTrackId = new Map((preview?.items ?? []).map((item) => [item.track_id, item]))
  const readyCount = preview?.ready_count ?? 0
  const blockedCount = preview?.blocked_count ?? 0
  const isProcessing = operation?.status === 'running'

  return (
    <main className="page inbox-page">
      <PageHeader
        title="Inbox"
        subtitle="Music being prepared. Imports are copied here; original source files are never modified."
        actions={
          <button className="btn btn--ghost btn--sm" disabled={loading} onClick={() => void load()}>
            <RefreshCw size={14} /> Refresh
          </button>
        }
      />

      {error && <StatusStrip tone="danger" onDismiss={() => setError(null)}>{error}</StatusStrip>}

      {loading && !status ? (
        <EmptyState title="Loading" message="Checking managed workspace status…" />
      ) : status?.state === 'not_configured' ? (
        <EmptyState
          icon={<FolderInput size={22} />}
          title="No managed workspace yet"
          message="Set up a managed workspace in Settings, then come back to Inbox to import music."
          action={<Link className="btn btn--primary" to="/settings#workspace">Set Up Workspace</Link>}
        />
      ) : status?.state === 'legacy_direct_library' ? (
        <EmptyState
          icon={<FolderInput size={22} />}
          title="Existing music folder detected"
          message="This is not a CrateIQ Managed Workspace. To use Inbox, create a dedicated workspace first."
          action={<Link className="btn btn--primary" to="/settings#workspace">Set Up Workspace</Link>}
        />
      ) : (
        <>
          <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>
            Imports are copied into {status?.inbox_path}. Originals are never modified.
          </StatusStrip>

          <section className="beets-review-kpis" aria-label="Inbox pipeline summary">
            <KpiCard tone="cyan" label="Imported" value={tracks?.total ?? 0} sub="Copied, not yet promoted" />
            <KpiCard tone="violet" label="Cleaned" value={operation?.cleaned_count ?? 0} sub="Last Process All run" />
            <KpiCard tone="violet" label="Enriched" value={operation?.enriched_count ?? 0} sub="Last Process All run" />
            <KpiCard tone="emerald" label="Ready" value={readyCount} sub="Artist, title, genre, verified" />
            <KpiCard tone="coral" label="Needs work" value={blockedCount} sub="Missing required fields" />
          </section>

          <div className="card settings-card">
            <h2 className="card-title"><Wand2 size={16} /> Process All</h2>
            {preflight && (
              <p className="muted">
                {preflight.inbox_total} tracks in Inbox — {preflight.already_ready} already ready,{' '}
                {preflight.need_cleaning} need cleaning, {preflight.need_enrichment} need identification,{' '}
                {preflight.likely_review} likely need manual review. Enrichment lookups are bounded to{' '}
                {preflight.enrichment_lookup_bound} tracks per run.
              </p>
            )}
            <div className="settings-actions">
              <button
                className="btn btn--primary"
                disabled={isProcessing || !tracks?.total}
                onClick={() => setConfirmingProcessAll(true)}
              >
                {isProcessing ? <Loader2 size={14} className="spin" /> : <Sparkles size={14} />}
                {isProcessing ? 'Processing…' : 'Process All'}
              </button>
              {isProcessing && (
                <button className="btn btn--ghost btn--sm" onClick={() => void doCancelProcessAll()}>
                  Cancel
                </button>
              )}
            </div>
            {confirmingProcessAll && !isProcessing && (
              <StatusStrip
                tone="warn"
                actions={
                  <>
                    <button className="btn btn--primary btn--sm" onClick={() => void doProcessAll()}>Confirm & process</button>
                    <button className="btn btn--ghost btn--sm" onClick={() => setConfirmingProcessAll(false)}>Cancel</button>
                  </>
                }
              >
                {preflight?.message}
              </StatusStrip>
            )}
            {operation && (
              <p className="muted">
                Last run ({operation.status}): {operation.cleaned_count} cleaned, {operation.enriched_count} enriched,{' '}
                {operation.written_count} written, {operation.ready_count} ready, {operation.needs_review_count} need review.
                {operation.warnings.length > 0 && ` ${operation.warnings.length} warning(s).`}
              </p>
            )}
          </div>

          <div className="card settings-card">
            <h2 className="card-title"><Upload size={16} /> Import music</h2>
            <label>
              Source paths
              <p className="muted">Paste one absolute file or folder path per line. Folders are imported recursively.</p>
              <textarea
                className="form-input"
                rows={3}
                value={importPaths}
                onChange={(event) => setImportPaths(event.target.value)}
                placeholder="/home/user/Downloads/new-tracks"
                disabled={importing}
              />
            </label>
            <div className="settings-actions">
              <button className="btn btn--primary" disabled={importing || !importPaths.trim()} onClick={() => void doImport()}>
                {importing ? 'Importing…' : 'Import Music'}
              </button>
            </div>
            {importResult && (
              <p className="muted">
                Copied {importResult.imported_count} file(s)
                {importResult.duplicate_count ? `, skipped ${importResult.duplicate_count} duplicate(s)` : ''}
                {importResult.failed_count ? `, ${importResult.failed_count} failed` : ''}.
              </p>
            )}
          </div>

          {!tracks?.items.length ? (
            <EmptyState icon={<InboxIcon size={22} />} title="Inbox is empty" message="Import music to begin preparing it for the Library." />
          ) : (
            <>
              <div className="settings-actions">
                <button className="btn btn--ghost btn--sm" disabled={!selected.size || batchBusy !== null} onClick={() => void doCleanSelected()}>
                  {batchBusy === 'clean' ? 'Cleaning…' : `Clean Selected (${selected.size})`}
                </button>
                <button className="btn btn--ghost btn--sm" disabled={!selected.size || batchBusy !== null} onClick={() => void doEnrichSelected()}>
                  {batchBusy === 'enrich' ? 'Enriching…' : `Enrich Selected (${selected.size})`}
                </button>
                <Link className="btn btn--ghost btn--sm" to="/needs-review">Open Needs Review</Link>
              </div>
              <div className="card settings-card table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>
                        <input
                          type="checkbox"
                          checked={selected.size > 0 && selected.size === tracks.items.length}
                          ref={(el) => { if (el) el.indeterminate = selected.size > 0 && selected.size < tracks.items.length }}
                          onChange={() => setSelected(
                            selected.size === tracks.items.length ? new Set() : new Set(tracks.items.map((t) => t.id)),
                          )}
                          aria-label="Select all Inbox tracks"
                        />
                      </th>
                      <th>Track</th><th>Artist</th><th>Title</th><th>Genre</th><th>BPM</th><th>Key</th><th>Readiness</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tracks.items.map((track) => {
                      const readiness = readinessByTrackId.get(track.id)
                      return (
                        <tr key={track.id}>
                          <td>
                            <input
                              type="checkbox"
                              checked={selected.has(track.id)}
                              onChange={() => toggleSelected(track.id)}
                              aria-label={`Select ${track.filename}`}
                            />
                          </td>
                          <td>{track.filename}</td>
                          <td>{track.artist || '—'}</td>
                          <td>{track.title || '—'}</td>
                          <td>{track.genre || '—'}</td>
                          <td>{track.bpm ?? '—'}</td>
                          <td>{track.key_camelot || track.key_musical || '—'}</td>
                          <td>
                            {readiness?.ready ? (
                              <Badge tone="succeeded">Ready</Badge>
                            ) : (
                              <span title={readiness?.blockers.join(' ')}>
                                <Badge tone="pending">{readiness?.blockers[0] || 'Needs review'}</Badge>
                              </span>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <div className="settings-actions">
            <button
              className="btn btn--primary"
              disabled={!readyCount || promoting}
              onClick={() => setConfirmingPromotion(true)}
            >
              Move Ready to Library ({readyCount})
            </button>
          </div>

          {confirmingPromotion && (
            <StatusStrip
              tone="warn"
              actions={
                <>
                  <button className="btn btn--primary btn--sm" disabled={promoting} onClick={() => void doPromote()}>
                    {promoting ? 'Moving…' : 'Confirm move'}
                  </button>
                  <button className="btn btn--ghost btn--sm" disabled={promoting} onClick={() => setConfirmingPromotion(false)}>
                    Cancel
                  </button>
                </>
              }
            >
              Move {readyCount} ready track(s) into {status?.library_path}? This moves the Inbox copies; it never touches the original imported files.
            </StatusStrip>
          )}
        </>
      )}
    </main>
  )
}
