import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, FolderInput, Inbox as InboxIcon, Loader2, Pencil, RefreshCw, ShieldCheck, Sparkles, Upload, Wand2, X } from 'lucide-react'
import { ApiError } from '../api/client'
import {
  applyInboxBulkEdit,
  applyPromotion,
  cancelPrepareOperation,
  cleanSelected,
  enrichSelected,
  fetchInboxTracks,
  fetchPreparePreview,
  fetchPrepareOperation,
  fetchWorkspaceStatus,
  importToInbox,
  patchInboxTrack,
  previewInboxBulkEdit,
  previewPromotion,
  startProcessAll,
} from '../api/workspace'
import type {
  InboxBulkEditApplyResult, InboxBulkEditPreview, InboxSortKey, InboxTrackPage,
  PreparationOperation, PreparePreflight, PromotionPreview, SortOrder,
  WorkspaceImportResult, WorkspaceStatus,
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

// ---------------------------------------------------------------------------
// Inline edit cell (Track/file basename, Artist, Genre)
// ---------------------------------------------------------------------------

function splitExt(filename: string): { base: string; ext: string } {
  const idx = filename.lastIndexOf('.')
  if (idx <= 0) return { base: filename, ext: '' }
  return { base: filename.slice(0, idx), ext: filename.slice(idx) }
}

interface EditableCellProps {
  value: string
  ariaLabel: string
  onSave: (nextValue: string) => Promise<void>
  onEditingChange: (editing: boolean) => void
  suffix?: string
  maxLength?: number
}

function EditableCell({ value, ariaLabel, onSave, onEditingChange, suffix, maxLength }: EditableCellProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { if (!editing) setDraft(value) }, [value, editing])
  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  const startEdit = () => {
    setDraft(value)
    setLocalError(null)
    setEditing(true)
    onEditingChange(true)
  }
  const stopEdit = () => {
    setEditing(false)
    onEditingChange(false)
  }
  const cancel = () => {
    setDraft(value)
    setLocalError(null)
    stopEdit()
  }
  const save = async () => {
    const next = draft.trim()
    if (!next) {
      setLocalError('Cannot be empty.')
      return
    }
    if (next === value.trim()) {
      stopEdit()
      return
    }
    setSaving(true)
    setLocalError(null)
    try {
      await onSave(next)
      stopEdit()
    } catch (err) {
      setLocalError(messageFor(err, 'Save failed.'))
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <button type="button" className="inbox-cell-edit-trigger" onClick={startEdit} aria-label={`Edit ${ariaLabel}`}>
        <span className="inbox-cell-value">{value || '—'}</span>
        <Pencil size={12} className="inbox-cell-pencil" aria-hidden="true" />
      </button>
    )
  }

  return (
    <span className="inbox-cell-editing">
      <span className="inbox-cell-input-row">
        <input
          ref={inputRef}
          className="inbox-cell-input"
          value={draft}
          maxLength={maxLength}
          disabled={saving}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') { event.preventDefault(); void save() }
            else if (event.key === 'Escape') { event.preventDefault(); cancel() }
          }}
          aria-label={`${ariaLabel} value`}
        />
        {suffix && <span className="inbox-cell-suffix">{suffix}</span>}
        <button type="button" className="icon-btn icon-btn--sm icon-btn--approve" disabled={saving} onClick={() => void save()} aria-label={`Save ${ariaLabel}`}>
          <Check size={13} />
        </button>
        <button type="button" className="icon-btn icon-btn--sm" disabled={saving} onClick={cancel} aria-label={`Cancel editing ${ariaLabel}`}>
          <X size={13} />
        </button>
      </span>
      {localError && <span className="inbox-cell-error">{localError}</span>}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

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
  const [sort, setSort] = useState<SortState>({ key: 'artist', order: 'asc' })
  const [activeEditCount, setActiveEditCount] = useState(0)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Bulk edit
  const [bulkEditOpen, setBulkEditOpen] = useState(false)
  const [bulkArtistEnabled, setBulkArtistEnabled] = useState(false)
  const [bulkArtistValue, setBulkArtistValue] = useState('')
  const [bulkGenreEnabled, setBulkGenreEnabled] = useState(false)
  const [bulkGenreValue, setBulkGenreValue] = useState('')
  const [bulkPreview, setBulkPreview] = useState<InboxBulkEditPreview | null>(null)
  const [bulkPreviewing, setBulkPreviewing] = useState(false)
  const [bulkApplying, setBulkApplying] = useState(false)
  const [bulkResult, setBulkResult] = useState<InboxBulkEditApplyResult | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const nextStatus = await fetchWorkspaceStatus()
      setStatus(nextStatus)
      if (nextStatus.state === 'managed_workspace') {
        const [nextTracks, nextPreview, nextPreflight] = await Promise.all([
          fetchInboxTracks({ limit: 200, sort: sort.key, order: sort.order }),
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
  }, [sort])

  useEffect(() => { void load() }, [load])
  useEffect(() => () => { if (pollRef.current) clearTimeout(pollRef.current) }, [])
  useEffect(() => { if (selected.size === 0) setBulkEditOpen(false) }, [selected])

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
  }

  const bulkFields = {
    ...(bulkArtistEnabled && bulkArtistValue.trim() ? { artist: bulkArtistValue.trim() } : {}),
    ...(bulkGenreEnabled && bulkGenreValue.trim() ? { genre: bulkGenreValue.trim() } : {}),
  }
  const bulkFieldsValid = Object.keys(bulkFields).length > 0

  const resetBulkResults = () => { setBulkPreview(null); setBulkResult(null) }

  const doBulkPreview = async () => {
    if (!bulkFieldsValid || !selected.size) return
    setBulkPreviewing(true)
    setError(null)
    try {
      const result = await previewInboxBulkEdit(Array.from(selected), bulkFields)
      setBulkPreview(result)
      setBulkResult(null)
    } catch (err) {
      setError(messageFor(err, 'Bulk edit preview failed.'))
    } finally {
      setBulkPreviewing(false)
    }
  }

  const doBulkApply = async () => {
    if (!bulkFieldsValid || !selected.size) return
    setBulkApplying(true)
    setError(null)
    try {
      const result = await applyInboxBulkEdit(Array.from(selected), bulkFields)
      setBulkResult(result)
      await load()
    } catch (err) {
      setError(messageFor(err, 'Bulk edit apply failed.'))
    } finally {
      setBulkApplying(false)
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
                <button
                  className="btn btn--ghost btn--sm"
                  disabled={!selected.size}
                  onClick={() => setBulkEditOpen((open) => !open)}
                  aria-expanded={bulkEditOpen}
                >
                  <Pencil size={14} /> Bulk Edit ({selected.size})
                </button>
                <Link className="btn btn--ghost btn--sm" to="/needs-review">Open Needs Review</Link>
              </div>

              {bulkEditOpen && (
                <div className="card settings-card inbox-bulk-edit">
                  <h2 className="card-title"><Pencil size={16} /> Bulk Edit — {selected.size} selected track{selected.size === 1 ? '' : 's'}</h2>
                  <div className="inbox-bulk-edit-fields">
                    <label className="inbox-bulk-edit-field">
                      <input
                        type="checkbox"
                        checked={bulkArtistEnabled}
                        onChange={(event) => { setBulkArtistEnabled(event.target.checked); resetBulkResults() }}
                      />
                      Set Artist
                      <input
                        className="form-input"
                        type="text"
                        value={bulkArtistValue}
                        disabled={!bulkArtistEnabled}
                        onChange={(event) => { setBulkArtistValue(event.target.value); resetBulkResults() }}
                        placeholder="New artist name"
                        aria-label="New artist value for bulk edit"
                      />
                    </label>
                    <label className="inbox-bulk-edit-field">
                      <input
                        type="checkbox"
                        checked={bulkGenreEnabled}
                        onChange={(event) => { setBulkGenreEnabled(event.target.checked); resetBulkResults() }}
                      />
                      Set Genre
                      <input
                        className="form-input"
                        type="text"
                        value={bulkGenreValue}
                        disabled={!bulkGenreEnabled}
                        onChange={(event) => { setBulkGenreValue(event.target.value); resetBulkResults() }}
                        placeholder="New genre"
                        aria-label="New genre value for bulk edit"
                      />
                    </label>
                  </div>
                  <div className="settings-actions">
                    <button className="btn btn--ghost btn--sm" disabled={bulkPreviewing || !bulkFieldsValid} onClick={() => void doBulkPreview()}>
                      {bulkPreviewing ? 'Loading preview…' : 'Preview'}
                    </button>
                    <button
                      className="btn btn--ghost btn--sm"
                      onClick={() => { setBulkEditOpen(false); resetBulkResults() }}
                    >
                      Close
                    </button>
                  </div>

                  {bulkPreview && (
                    <div className="inbox-bulk-edit-preview">
                      <p className="muted">
                        {bulkPreview.selected_count} selected track{bulkPreview.selected_count === 1 ? '' : 's'}
                        {bulkPreview.skipped_not_inbox ? ` — ${bulkPreview.skipped_not_inbox} not in Inbox will be skipped` : ''}
                      </p>
                      {bulkPreview.fields.artist && (
                        <div className="inbox-bulk-edit-preview-field">
                          <strong>Artist</strong>
                          <p className="muted">Current values include: {bulkPreview.fields.artist.current_values.join(', ')}</p>
                          <p>New value: <strong>{bulkPreview.fields.artist.new_value}</strong></p>
                        </div>
                      )}
                      {bulkPreview.fields.genre && (
                        <div className="inbox-bulk-edit-preview-field">
                          <strong>Genre</strong>
                          <p className="muted">Current values include: {bulkPreview.fields.genre.current_values.join(', ')}</p>
                          <p>New value: <strong>{bulkPreview.fields.genre.new_value}</strong></p>
                        </div>
                      )}
                      <div className="settings-actions">
                        <button className="btn btn--primary btn--sm" disabled={bulkApplying} onClick={() => void doBulkApply()}>
                          {bulkApplying ? 'Applying…' : `Apply to ${bulkPreview.eligible_count} track${bulkPreview.eligible_count === 1 ? '' : 's'}`}
                        </button>
                      </div>
                    </div>
                  )}

                  {bulkResult && (
                    <StatusStrip tone={bulkResult.failed_count ? 'warn' : 'good'}>
                      {bulkResult.succeeded_count} succeeded, {bulkResult.unchanged_count} unchanged
                      {bulkResult.skipped_count ? `, ${bulkResult.skipped_count} skipped` : ''}
                      {bulkResult.failed_count ? `, ${bulkResult.failed_count} failed` : ''}.
                    </StatusStrip>
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
                          checked={selected.size > 0 && selected.size === tracks.items.length}
                          ref={(el) => { if (el) el.indeterminate = selected.size > 0 && selected.size < tracks.items.length }}
                          onChange={() => setSelected(
                            selected.size === tracks.items.length ? new Set() : new Set(tracks.items.map((t) => t.id)),
                          )}
                          aria-label="Select all Inbox tracks"
                        />
                      </th>
                      <SortTh label="Track / file" sortKey="filename" sort={sort} onSort={onSort} title="Managed Inbox filename — click to sort" />
                      <SortTh label="Artist" sortKey="artist" sort={sort} onSort={onSort} />
                      <SortTh label="Title" sortKey="title" sort={sort} onSort={onSort} />
                      <SortTh label="Genre" sortKey="genre" sort={sort} onSort={onSort} />
                      <SortTh label="BPM" sortKey="bpm" sort={sort} onSort={onSort} />
                      <SortTh label="Key" sortKey="key" sort={sort} onSort={onSort} />
                      <SortTh label="Readiness" sortKey="readiness" sort={sort} onSort={onSort} />
                    </tr>
                  </thead>
                  <tbody>
                    {tracks.items.map((track) => {
                      const readiness = readinessByTrackId.get(track.id)
                      const { base, ext } = splitExt(track.filename)
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
                          <td>
                            <EditableCell
                              value={base}
                              suffix={ext}
                              ariaLabel={`Track filename for ${track.filename}`}
                              onEditingChange={(editing) => setActiveEditCount((n) => Math.max(0, n + (editing ? 1 : -1)))}
                              onSave={async (nextBase) => {
                                await patchInboxTrack(track.id, { filename: nextBase })
                                await load()
                              }}
                            />
                          </td>
                          <td>
                            <EditableCell
                              value={track.artist ?? ''}
                              ariaLabel={`Artist for ${track.filename}`}
                              onEditingChange={(editing) => setActiveEditCount((n) => Math.max(0, n + (editing ? 1 : -1)))}
                              onSave={async (next) => {
                                await patchInboxTrack(track.id, { artist: next })
                                await load()
                              }}
                            />
                          </td>
                          <td>{track.title || '—'}</td>
                          <td>
                            <EditableCell
                              value={track.genre ?? ''}
                              ariaLabel={`Genre for ${track.filename}`}
                              onEditingChange={(editing) => setActiveEditCount((n) => Math.max(0, n + (editing ? 1 : -1)))}
                              onSave={async (next) => {
                                await patchInboxTrack(track.id, { genre: next })
                                await load()
                              }}
                            />
                          </td>
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
