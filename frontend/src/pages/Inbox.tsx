import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ChevronRight, FileCheck2, FolderInput, Inbox as InboxIcon, ListChecks, Loader2, Pencil, RefreshCw, ShieldCheck, Sparkles, Upload, Wand2 } from 'lucide-react'
import { ApiError } from '../api/client'
import {
  applyPromotion,
  cancelPrepareOperation,
  cleanSelected,
  enrichSelected,
  fetchInboxTrackInspection,
  fetchInboxTracks,
  fetchPreparePreview,
  fetchPrepareOperation,
  fetchWorkspaceStatus,
  importToInbox,
  patchInboxTrack,
  previewPromotion,
  startProcessAll,
} from '../api/workspace'
import type {
  InboxSortKey, InboxTrackMetadataEditResult, InboxTrackPage,
  PreparationOperation, PreparePreflight, PromotionPreview, SortOrder,
  WorkspaceImportResult, WorkspaceStatus,
} from '../api/workspace'
import type { InboxEditableMetadataField, TrackSummary } from '../types/track'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'
import InboxFilters from '../components/inbox/InboxFilters'
import type { InboxStatusFilter } from '../components/inbox/InboxFilters'
import InboxSelectionBar from '../components/inbox/InboxSelectionBar'
import InboxTrackInspector from '../components/inbox/InboxTrackInspector'
import PreparationStatusBadge from '../components/inbox/PreparationStatusBadge'
import EditableMetadataCell from '../components/inbox/EditableMetadataCell'
import { useInboxSelection } from '../hooks/useInboxSelection'
import SaveToFileDialog from '../components/inbox/SaveToFileDialog'
import EnrichmentSourceDialog from '../components/inbox/EnrichmentSourceDialog'
import BulkEnrichmentReview from '../components/inbox/BulkEnrichmentReview'
import BulkMetadataEditor from '../components/inbox/BulkMetadataEditor'
import type { BulkEnrichmentSummary } from '../types/enrichmentReview'
import { fetchBulkEnrichmentSummary } from '../api/enrichmentReview'

function messageFor(error: unknown, fallback: string) {
  if (error instanceof ApiError) return error.displayMessage
  if (error instanceof Error && error.message) return error.message
  return fallback
}

const POLL_INTERVAL_MS = 1500
const BULK_SELECTION_LIMIT = 200

interface SortState {
  key: InboxSortKey
  order: SortOrder
}

// ---------------------------------------------------------------------------
// Sortable column header
// ---------------------------------------------------------------------------

interface SortThProps {
  label: string
  sortKey: InboxSortKey
  sort: SortState
  onSort: (key: InboxSortKey) => void
  title?: string
}

function SortTh({ label, sortKey, sort, onSort, title }: SortThProps) {
  const active = sort.key === sortKey
  const ariaSort: 'ascending' | 'descending' | 'none' = active ? (sort.order === 'asc' ? 'ascending' : 'descending') : 'none'
  return (
    <th className={`th-sortable${active ? ' th-sortable--active' : ''}`} aria-sort={ariaSort}>
      <button type="button" className="th-sortable-button" onClick={() => onSort(sortKey)} title={title ?? `Sort by ${label}`}>
        {label}
        <span className="sort-indicator" aria-hidden="true">{active ? (sort.order === 'asc' ? ' ▲' : ' ▼') : ' ⇅'}</span>
      </button>
    </th>
  )
}

function splitExt(filename: string): { base: string; ext: string } {
  const idx = filename.lastIndexOf('.')
  if (idx <= 0) return { base: filename, ext: '' }
  return { base: filename.slice(0, idx), ext: filename.slice(idx) }
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Inbox() {
  const [searchParams, setSearchParams] = useSearchParams()
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
  const [batchBusy, setBatchBusy] = useState<'clean' | 'enrich' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sort, setSort] = useState<SortState>({ key: 'artist', order: 'asc' })
  const [searchDraft, setSearchDraft] = useState('')
  const [search, setSearch] = useState('')
  const [preparationFilter, setPreparationFilter] = useState<InboxStatusFilter>('ALL')
  const [offset, setOffset] = useState(0)
  const [activeEditCount, setActiveEditCount] = useState(0)
  const [inspectedTrack, setInspectedTrack] = useState<TrackSummary | null>(null)
  const [inspectorLoading, setInspectorLoading] = useState(false)
  const [knownTracks, setKnownTracks] = useState<Record<number, TrackSummary>>({})
  const [saveTrackIds, setSaveTrackIds] = useState<number[] | null>(null)
  const [enrichmentSourceDialogOpen, setEnrichmentSourceDialogOpen] = useState(false)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const loadRequestRef = useRef(0)
  const inspectorTriggerRef = useRef<HTMLElement | null>(null)
  const bulkPanelRef = useRef<HTMLDivElement | null>(null)

  const visibleIds = useMemo(() => tracks?.items.map((track) => track.id) ?? [], [tracks])
  const availableIds = useMemo(() => tracks?.available_track_ids ?? null, [tracks])
  const selection = useInboxSelection(visibleIds, availableIds)
  const { selectedIds, selectedCount, visibleSelectedCount, hiddenSelectedCount } = selection
  const selectedTrackIds = useMemo(() => Array.from(selectedIds), [selectedIds])
  const inspectedParam = searchParams.get('track')
  const inspectedId = inspectedParam && /^\d+$/.test(inspectedParam) ? Number(inspectedParam) : null

  const [bulkEditOpen, setBulkEditOpen] = useState(false)
  const [bulkReviewOpen, setBulkReviewOpen] = useState(false)
  const [bulkReviewRefreshKey, setBulkReviewRefreshKey] = useState(0)
  const [exceptionTrackIds, setExceptionTrackIds] = useState<number[]>([])
  const [inspectorInitialTab, setInspectorInitialTab] = useState<'status' | 'review'>('status')
  const bulkSelectionTooLarge = selectedCount > BULK_SELECTION_LIMIT

  const load = useCallback(async () => {
    const requestId = ++loadRequestRef.current
    setLoading(true)
    setError(null)
    try {
      const nextStatus = await fetchWorkspaceStatus()
      if (requestId !== loadRequestRef.current) return
      setStatus(nextStatus)
      if (nextStatus.state === 'managed_workspace') {
        const [nextTracks, nextPreview, nextPreflight] = await Promise.all([
          fetchInboxTracks({
            search: search || undefined,
            preparation_status: preparationFilter === 'ALL' ? undefined : preparationFilter,
            limit: 200,
            offset,
            sort: sort.key,
            order: sort.order,
          }),
          previewPromotion(),
          fetchPreparePreview(),
        ])
        if (requestId !== loadRequestRef.current) return
        setTracks(nextTracks)
        setKnownTracks((current) => Object.fromEntries([
          ...Object.entries(current),
          ...nextTracks.items.map((track) => [track.id, track]),
        ]))
        setPreview(nextPreview)
        setPreflight(nextPreflight)
      } else {
        setTracks(null)
        setPreview(null)
        setPreflight(null)
      }
    } catch (err) {
      if (requestId !== loadRequestRef.current) return
      setError(messageFor(err, 'Could not load the managed workspace.'))
    } finally {
      if (requestId === loadRequestRef.current) setLoading(false)
    }
  }, [offset, preparationFilter, search, sort])

  useEffect(() => { void load() }, [load])
  useEffect(() => () => { if (pollRef.current) clearTimeout(pollRef.current) }, [])
  useEffect(() => {
    if (selectedCount === 0 || bulkSelectionTooLarge) {
      setBulkEditOpen(false)
      setBulkReviewOpen(false)
      setExceptionTrackIds([])
    }
  }, [bulkSelectionTooLarge, selectedCount])
  useEffect(() => {
    if (!bulkEditOpen && !bulkReviewOpen) return
    const timer = window.setTimeout(() => bulkPanelRef.current?.scrollIntoView?.({ block: 'nearest' }), 0)
    return () => window.clearTimeout(timer)
  }, [bulkEditOpen, bulkReviewOpen])
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchDraft.trim())
      setOffset(0)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [searchDraft])

  const closeInspector = useCallback(() => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('track')
      return next
    })
    setInspectedTrack(null)
    setExceptionTrackIds([])
    window.setTimeout(() => inspectorTriggerRef.current?.focus(), 0)
  }, [setSearchParams])

  const openInspector = useCallback((track: TrackSummary, trigger: HTMLElement) => {
    inspectorTriggerRef.current = trigger
    setInspectorInitialTab('status')
    setExceptionTrackIds([])
    setInspectedTrack(track)
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.set('track', String(track.id))
      return next
    })
  }, [setSearchParams])

  useEffect(() => {
    if (inspectedId === null) {
      setInspectedTrack(null)
      return
    }
    const visible = tracks?.items.find((track) => track.id === inspectedId)
    if (visible) {
      setInspectedTrack(visible)
      setInspectorLoading(false)
      return
    }
    if (tracks && !tracks.available_track_ids.includes(inspectedId)) {
      closeInspector()
      return
    }
    let cancelled = false
    setInspectorLoading(true)
    fetchInboxTrackInspection(inspectedId)
      .then((track) => { if (!cancelled) setInspectedTrack(track) })
      .catch((err) => {
        if (cancelled) return
        setError(messageFor(err, 'Could not load the Inbox track inspector.'))
        closeInspector()
      })
      .finally(() => { if (!cancelled) setInspectorLoading(false) })
    return () => { cancelled = true }
  }, [closeInspector, inspectedId, tracks])

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

  const doCleanSelected = async () => {
    if (!selectedCount) return
    setBatchBusy('clean')
    setError(null)
    try {
      await cleanSelected(selectedTrackIds)
      await load()
    } catch (err) {
      setError(messageFor(err, 'Clean Selected failed.'))
    } finally {
      setBatchBusy(null)
    }
  }

  const openEnrichmentSourceDialog = () => {
    if (!selectedCount) return
    setError(null)
    setEnrichmentSourceDialogOpen(true)
  }

  const doEnrichSelected = async (sourceIds: string[]) => {
    if (!selectedCount) return
    setBatchBusy('enrich')
    setError(null)
    try {
      await enrichSelected(selectedTrackIds, sourceIds)
      await load()
    } catch (err) {
      setError(messageFor(err, 'Enrich Selected failed.'))
    } finally {
      setBatchBusy(null)
    }
  }

  const fetchCurrentInboxData = useCallback(async () => {
    const requestId = ++loadRequestRef.current
    try {
      const [nextTracks, nextPreview] = await Promise.all([
        fetchInboxTracks({
          search: search || undefined,
          preparation_status: preparationFilter === 'ALL' ? undefined : preparationFilter,
          limit: 200,
          offset,
          sort: sort.key,
          order: sort.order,
        }),
        previewPromotion(),
      ])
      if (requestId !== loadRequestRef.current) return
      setTracks(nextTracks)
      setKnownTracks((current) => Object.fromEntries([
        ...Object.entries(current),
        ...nextTracks.items.map((track) => [track.id, track]),
      ]))
      setPreview(nextPreview)
    } catch (err) {
      if (requestId === loadRequestRef.current) setError(messageFor(err, 'Could not refresh Inbox metadata.'))
    }
  }, [offset, preparationFilter, search, sort])

  const patchLocalMetadata = useCallback((metadata: InboxTrackMetadataEditResult) => {
    const patch = {
      artist: metadata.artist,
      title: metadata.title,
      genre: metadata.genre,
      album: metadata.album,
      preparation_state: metadata.preparation_state,
    }
    setTracks((current) => current
      ? { ...current, items: current.items.map((item) => item.id === metadata.track_id ? { ...item, ...patch } : item) }
      : current)
    setInspectedTrack((current) => current && current.id === metadata.track_id ? { ...current, ...patch } : current)
  }, [])

  const saveMetadata = useCallback(async (trackId: number, field: InboxEditableMetadataField, value: string) => {
    const response = await patchInboxTrack(trackId, { [field]: value })
    if (response.errors.length) throw new Error(response.errors.join('; '))
    if (!response.metadata) throw new Error('The metadata edit did not return an authoritative result.')
    patchLocalMetadata(response.metadata)
    await fetchCurrentInboxData()
  }, [fetchCurrentInboxData, patchLocalMetadata])

  const saveFilename = useCallback(async (trackId: number, filename: string) => {
    const response = await patchInboxTrack(trackId, { filename })
    if (response.errors.length) throw new Error(response.errors.join('; '))
    if (response.rename) {
      setTracks((current) => current
        ? { ...current, items: current.items.map((item) => item.id === trackId
          ? { ...item, filename: response.rename!.filename, filepath: response.rename!.filepath }
          : item) }
        : current)
      setInspectedTrack((current) => current && current.id === trackId
        ? { ...current, filename: response.rename!.filename, filepath: response.rename!.filepath }
        : current)
    }
    await fetchCurrentInboxData()
  }, [fetchCurrentInboxData])

  const onSort = (key: InboxSortKey) => {
    if (activeEditCount > 0) {
      setError('Finish or cancel the open edit before changing the sort order.')
      return
    }
    setSort((current) => (
      current.key === key
        ? { key, order: current.order === 'asc' ? 'desc' : 'asc' }
        : { key, order: 'asc' }
    ))
    setOffset(0)
  }

  const readyCount = preview?.ready_count ?? 0
  const blockedCount = preview?.blocked_count ?? 0
  const saveSelectionKnown = selectedTrackIds.every((trackId) => Boolean(knownTracks[trackId]?.preparation_state))
  const selectedUnsavedWritableCount = selectedTrackIds.filter((trackId) => {
    const preparation = knownTracks[trackId]?.preparation_state
    return Boolean(preparation?.write.has_unsaved_changes && !preparation.write.blocked)
  }).length
  const saveToFileLabel = saveSelectionKnown ? `Save to File (${selectedUnsavedWritableCount})` : 'Save to File'
  const isProcessing = operation?.status === 'running'
  const openInspectorById = async (trackId: number) => {
    try {
      const track = knownTracks[trackId] ?? await fetchInboxTrackInspection(trackId)
      setInspectedTrack(track)
      setSearchParams((current) => {
        const next = new URLSearchParams(current)
        next.set('track', String(trackId))
        return next
      })
    } catch (err) {
      setError(messageFor(err, 'Could not open the selected review exception.'))
    }
  }
  const openExceptionReview = (trackIds: number[]) => {
    if (!trackIds.length) return
    inspectorTriggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setExceptionTrackIds(trackIds)
    setInspectorInitialTab('review')
    void openInspectorById(trackIds[0])
  }
  const afterBulkReview = async (summary: BulkEnrichmentSummary) => {
    setExceptionTrackIds(summary.rows.filter((row) => row.review_state === 'exception').map((row) => row.track_id))
    setBulkReviewRefreshKey((value) => value + 1)
    await fetchCurrentInboxData()
  }
  const afterInspectorReviewDecision = async () => {
    await fetchCurrentInboxData()
    if (!exceptionTrackIds.length) return
    try {
      const summary = await fetchBulkEnrichmentSummary(selectedTrackIds)
      const remaining = summary.rows.filter((row) => row.review_state === 'exception').map((row) => row.track_id)
      setExceptionTrackIds(remaining)
      setBulkReviewRefreshKey((value) => value + 1)
      if (inspectedId !== null && !remaining.includes(inspectedId) && remaining.length) {
        await openInspectorById(remaining[0])
      }
    } catch (err) {
      setError(messageFor(err, 'Could not refresh the exception queue.'))
    }
  }
  const inspectorQueueIds = exceptionTrackIds.length ? exceptionTrackIds : visibleIds
  const inspectedQueueIndex = inspectedId === null ? -1 : inspectorQueueIds.indexOf(inspectedId)
  const previousInspectorId = inspectedQueueIndex > 0 ? inspectorQueueIds[inspectedQueueIndex - 1] : undefined
  const nextInspectorId = inspectedQueueIndex >= 0 && inspectedQueueIndex < inspectorQueueIds.length - 1
    ? inspectorQueueIds[inspectedQueueIndex + 1]
    : undefined
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
            <KpiCard tone="cyan" label="Imported" value={tracks?.available_track_ids.length ?? 0} sub="Copied, not yet promoted" />
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
                disabled={isProcessing || !tracks?.available_track_ids.length}
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

          <InboxFilters
            search={searchDraft}
            onSearchChange={setSearchDraft}
            status={preparationFilter}
            onStatusChange={(next) => { setPreparationFilter(next); setOffset(0) }}
            counts={tracks?.status_counts ?? {}}
          />

          <InboxSelectionBar
            selectedCount={selectedCount}
            visibleSelectedCount={visibleSelectedCount}
            hiddenSelectedCount={hiddenSelectedCount}
            bulkLimit={BULK_SELECTION_LIMIT}
            onClear={selection.clear}
            onClearHidden={selection.clearHidden}
          />

          {!tracks?.items.length ? (
            <EmptyState
              icon={<InboxIcon size={22} />}
              title={preview?.track_count ? 'No matching Inbox tracks' : 'Inbox is empty'}
              message={preview?.track_count
                ? 'Try another search or preparation status.'
                : 'Import music to begin preparing it for the Library.'}
            />
          ) : (
            <>
              <div className={`settings-actions inbox-bulk-action-bar${selectedCount ? ' is-active' : ''}`}>
                <button className="btn btn--ghost btn--sm" disabled={!selectedCount || batchBusy !== null} onClick={() => void doCleanSelected()}>
                  {batchBusy === 'clean' ? 'Cleaning…' : `Clean Selected (${selectedCount})`}
                </button>
                <button className="btn btn--ghost btn--sm" disabled={!selectedCount || batchBusy !== null} onClick={openEnrichmentSourceDialog}>
                  {batchBusy === 'enrich' ? 'Enriching…' : `Enrich Selected (${selectedCount})`}
                </button>
                <button
                  className="btn btn--ghost btn--sm"
                  disabled={!selectedCount || bulkSelectionTooLarge}
                  title={bulkSelectionTooLarge ? `Bulk Review supports at most ${BULK_SELECTION_LIMIT} tracks.` : undefined}
                  onClick={() => { setBulkReviewOpen((open) => !open); setBulkEditOpen(false) }}
                  aria-expanded={bulkReviewOpen}
                >
                  <ListChecks size={14} /> Bulk Review ({selectedCount})
                </button>
                <button
                  className="btn btn--ghost btn--sm"
                  disabled={!selectedCount || bulkSelectionTooLarge}
                  title={bulkSelectionTooLarge ? `Edit Metadata supports at most ${BULK_SELECTION_LIMIT} tracks.` : undefined}
                  onClick={() => { setBulkEditOpen((open) => !open); setBulkReviewOpen(false) }}
                  aria-expanded={bulkEditOpen}
                >
                  <Pencil size={14} /> Edit Metadata ({selectedCount})
                </button>
                <button
                  className="btn btn--primary btn--sm"
                  disabled={!selectedCount || (saveSelectionKnown && selectedUnsavedWritableCount === 0) || batchBusy !== null}
                  onClick={() => setSaveTrackIds(selectedTrackIds)}
                  title={selectedCount && saveSelectionKnown && selectedUnsavedWritableCount === 0 ? 'No selected tracks have current unsaved writable metadata.' : undefined}
                >
                  <FileCheck2 size={14} /> {saveToFileLabel}
                </button>
                <Link className="btn btn--ghost btn--sm" to="/needs-review">Open Needs Review</Link>
              </div>

              {bulkSelectionTooLarge && (
                <StatusStrip tone="warn">
                  Bulk Review and Edit Metadata support up to {BULK_SELECTION_LIMIT} tracks. Narrow or clear the selection to continue.
                </StatusStrip>
              )}

              {(bulkReviewOpen || bulkEditOpen) && (
                <div ref={bulkPanelRef}>
                  {bulkReviewOpen && (
                    <BulkEnrichmentReview
                      trackIds={selectedTrackIds}
                      refreshKey={bulkReviewRefreshKey}
                      onReviewExceptions={openExceptionReview}
                      onResolved={afterBulkReview}
                      onClose={() => setBulkReviewOpen(false)}
                    />
                  )}
                  {bulkEditOpen && (
                    <BulkMetadataEditor
                      key={selectedTrackIds.join(',')}
                      trackIds={selectedTrackIds}
                      tracks={selectedTrackIds.map((trackId) => knownTracks[trackId]).filter(Boolean)}
                      onApplied={fetchCurrentInboxData}
                      onClose={() => setBulkEditOpen(false)}
                    />
                  )}
                </div>
              )}

              <div className="card settings-card table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>
                        <input
                          type="checkbox"
                          checked={selection.allVisibleSelected}
                          ref={(el) => { if (el) el.indeterminate = visibleSelectedCount > 0 && !selection.allVisibleSelected }}
                          onChange={selection.toggleVisible}
                          aria-label={`Select visible page (${tracks.items.length} tracks)`}
                        />
                      </th>
                      <SortTh label="Track / filename" sortKey="filename" sort={sort} onSort={onSort} title="Managed Inbox filename — click to sort" />
                      <SortTh label="Artist" sortKey="artist" sort={sort} onSort={onSort} />
                      <SortTh label="Title" sortKey="title" sort={sort} onSort={onSort} title="Metadata title — does not rename the managed file" />
                      <SortTh label="Genre" sortKey="genre" sort={sort} onSort={onSort} />
                      <SortTh label="BPM" sortKey="bpm" sort={sort} onSort={onSort} />
                      <SortTh label="Key" sortKey="key" sort={sort} onSort={onSort} />
                      <SortTh label="Status" sortKey="readiness" sort={sort} onSort={onSort} />
                      <th><span className="lib-visually-hidden">Track details</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {tracks.items.map((track) => {
                      const { base, ext } = splitExt(track.filename)
                      return (
                        <tr key={track.id}>
                          <td>
                            <input
                              type="checkbox"
                              checked={selectedIds.has(track.id)}
                              onClick={(event) => selection.toggle(track.id, event.shiftKey)}
                              onChange={() => undefined}
                              aria-label={`Select ${track.filename}`}
                            />
                          </td>
                          <td>
                            <EditableMetadataCell
                              value={base}
                              suffix={ext}
                              ariaLabel={`Managed filename for ${track.filename}`}
                              onEditingChange={(editing) => setActiveEditCount((n) => Math.max(0, n + (editing ? 1 : -1)))}
                              onSave={(nextBase) => saveFilename(track.id, nextBase)}
                            />
                          </td>
                          <td>
                            <EditableMetadataCell
                              value={track.artist ?? ''}
                              ariaLabel={`Artist for ${track.filename}`}
                              onEditingChange={(editing) => setActiveEditCount((n) => Math.max(0, n + (editing ? 1 : -1)))}
                              onSave={(next) => saveMetadata(track.id, 'artist', next)}
                            />
                          </td>
                          <td>
                            <EditableMetadataCell
                              value={track.title ?? ''}
                              ariaLabel={`Title for ${track.filename}`}
                              onEditingChange={(editing) => setActiveEditCount((n) => Math.max(0, n + (editing ? 1 : -1)))}
                              onSave={(next) => saveMetadata(track.id, 'title', next)}
                            />
                          </td>
                          <td>
                            <EditableMetadataCell
                              value={track.genre ?? ''}
                              ariaLabel={`Genre for ${track.filename}`}
                              onEditingChange={(editing) => setActiveEditCount((n) => Math.max(0, n + (editing ? 1 : -1)))}
                              onSave={(next) => saveMetadata(track.id, 'genre', next)}
                            />
                          </td>
                          <td>{track.bpm ?? '—'}</td>
                          <td>{track.key_camelot || track.key_musical || '—'}</td>
                          <td>
                            <PreparationStatusBadge state={track.preparation_state} />
                            {track.preparation_state?.review_count ? (
                              <span className="inbox-review-count" aria-label={`${track.preparation_state.review_count} enrichment suggestion${track.preparation_state.review_count === 1 ? '' : 's'} need review`}>
                                {track.preparation_state.review_count} suggestion{track.preparation_state.review_count === 1 ? '' : 's'}
                              </span>
                            ) : null}
                          </td>
                          <td>
                            <button
                              type="button"
                              className="icon-btn icon-btn--sm inbox-inspect-trigger"
                              aria-label={`Inspect ${track.filename}`}
                              onClick={(event) => openInspector(track, event.currentTarget)}
                            >
                              <ChevronRight size={15} />
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <nav className="inbox-pagination" aria-label="Inbox pages">
                <span>
                  {tracks.total === 0 ? '0' : `${tracks.offset + 1}–${Math.min(tracks.offset + tracks.items.length, tracks.total)}`} of {tracks.total} matching
                </span>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={tracks.offset === 0}
                  onClick={() => setOffset(Math.max(0, tracks.offset - tracks.limit))}
                >
                  Previous page
                </button>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={tracks.offset + tracks.items.length >= tracks.total}
                  onClick={() => setOffset(tracks.offset + tracks.limit)}
                >
                  Next page
                </button>
              </nav>
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

          {inspectedId !== null && (
            <InboxTrackInspector
              track={inspectedTrack}
              loading={inspectorLoading}
              onClose={closeInspector}
              initialTab={inspectorInitialTab}
              navigationLabel={exceptionTrackIds.length && inspectedQueueIndex >= 0 ? `Exception ${inspectedQueueIndex + 1} of ${exceptionTrackIds.length}` : undefined}
              onPrevious={previousInspectorId ? () => void openInspectorById(previousInspectorId) : undefined}
              onNext={nextInspectorId ? () => void openInspectorById(nextInspectorId) : undefined}
              onMetadataSave={(field, value) => inspectedId === null ? Promise.resolve() : saveMetadata(inspectedId, field, value)}
              onSaveToFile={() => inspectedId !== null && setSaveTrackIds([inspectedId])}
              onReviewDecision={afterInspectorReviewDecision}
            />
          )}
          {saveTrackIds && (
            <SaveToFileDialog
              trackIds={saveTrackIds}
              onClose={() => setSaveTrackIds(null)}
              onApplied={() => void load()}
            />
          )}
          {enrichmentSourceDialogOpen && (
            <EnrichmentSourceDialog
              trackCount={selectedCount}
              onClose={() => setEnrichmentSourceDialogOpen(false)}
              onConfirm={doEnrichSelected}
            />
          )}
        </>
      )}
    </main>
  )
}
