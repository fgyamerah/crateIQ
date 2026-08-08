import { useCallback, useEffect, useState } from 'react'
import { FolderInput, Inbox as InboxIcon, RefreshCw, ShieldCheck, Upload } from 'lucide-react'
import { ApiError } from '../api/client'
import {
  applyPromotion,
  configureWorkspace,
  fetchInboxTracks,
  fetchWorkspaceStatus,
  importToInbox,
  previewPromotion,
} from '../api/workspace'
import type { InboxTrackPage, PromotionPreview, WorkspaceImportResult, WorkspaceStatus } from '../api/workspace'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'

function messageFor(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.displayMessage : fallback
}

export default function Inbox() {
  const [status, setStatus] = useState<WorkspaceStatus | null>(null)
  const [tracks, setTracks] = useState<InboxTrackPage | null>(null)
  const [preview, setPreview] = useState<PromotionPreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [configuring, setConfiguring] = useState(false)
  const [importing, setImporting] = useState(false)
  const [promoting, setPromoting] = useState(false)
  const [importPaths, setImportPaths] = useState('')
  const [importResult, setImportResult] = useState<WorkspaceImportResult | null>(null)
  const [confirmingPromotion, setConfirmingPromotion] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const nextStatus = await fetchWorkspaceStatus()
      setStatus(nextStatus)
      if (nextStatus.state === 'managed_workspace') {
        const [nextTracks, nextPreview] = await Promise.all([
          fetchInboxTracks({ limit: 200 }),
          previewPromotion(),
        ])
        setTracks(nextTracks)
        setPreview(nextPreview)
      } else {
        setTracks(null)
        setPreview(null)
      }
    } catch (err) {
      setError(messageFor(err, 'Could not load the managed workspace.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const doConfigure = async () => {
    setConfiguring(true)
    setError(null)
    try {
      await configureWorkspace()
      await load()
    } catch (err) {
      setError(messageFor(err, 'Could not configure the managed workspace.'))
    } finally {
      setConfiguring(false)
    }
  }

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

  const readinessByTrackId = new Map((preview?.items ?? []).map((item) => [item.track_id, item]))
  const readyCount = preview?.ready_count ?? 0
  const blockedCount = preview?.blocked_count ?? 0

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
          message={status.message}
          action={
            <button className="btn btn--primary" disabled={configuring} onClick={() => void doConfigure()}>
              {configuring ? 'Configuring…' : 'Configure managed workspace'}
            </button>
          }
        />
      ) : status?.state === 'legacy_direct_library' ? (
        <EmptyState
          icon={<FolderInput size={22} />}
          title="Existing direct library detected"
          message={status.message}
        />
      ) : (
        <>
          <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>
            Imports are copied into {status?.inbox_path}. Originals are never modified.
          </StatusStrip>

          <section className="beets-review-kpis" aria-label="Inbox summary">
            <KpiCard tone="cyan" label="Inbox tracks" value={tracks?.total ?? 0} sub="Copied, not yet promoted" />
            <KpiCard tone="emerald" label="Ready" value={readyCount} sub="Artist, title, genre, verified" />
            <KpiCard tone="coral" label="Needs work" value={blockedCount} sub="Missing required fields" />
          </section>

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
            <div className="card settings-card table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Track</th><th>Artist</th><th>Title</th><th>Genre</th><th>BPM</th><th>Key</th><th>Readiness</th>
                  </tr>
                </thead>
                <tbody>
                  {tracks.items.map((track) => {
                    const readiness = readinessByTrackId.get(track.id)
                    return (
                      <tr key={track.id}>
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
