import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Check, Loader2, Pencil } from 'lucide-react'
import { ApiError } from '../../api/client'
import {
  applyInboxBulkEdit,
  previewInboxBulkEdit,
  type InboxBulkEditApplyResult,
  type InboxBulkEditOperation,
  type InboxBulkEditOperationName,
  type InboxBulkEditPreview,
} from '../../api/workspace'
import type { InboxBulkMetadataField, TrackSummary } from '../../types/track'
import StatusStrip from '../ui/StatusStrip'

interface Props {
  trackIds: number[]
  tracks: TrackSummary[]
  onApplied: () => Promise<void> | void
  onClose: () => void
}

const FIELDS: Array<{
  id: InboxBulkMetadataField
  label: string
  operations: Array<{ value: InboxBulkEditOperationName; label: string }>
  placeholder: string
}> = [
  { id: 'genre', label: 'Genre', operations: [{ value: 'leave', label: 'Leave unchanged' }, { value: 'set', label: 'Set value' }, { value: 'clear', label: 'Clear value' }], placeholder: 'Afro House' },
  { id: 'comment', label: 'Comment', operations: [{ value: 'leave', label: 'Leave unchanged' }, { value: 'set', label: 'Set value' }, { value: 'append', label: 'Append value' }, { value: 'clear', label: 'Clear value' }], placeholder: 'Warm-up' },
  { id: 'label', label: 'Label', operations: [{ value: 'leave', label: 'Leave unchanged' }, { value: 'set', label: 'Set value' }, { value: 'clear', label: 'Clear value' }], placeholder: 'Record label' },
]

const initialOperations = (): Record<InboxBulkMetadataField, InboxBulkEditOperation> => ({
  genre: { operation: 'leave' },
  comment: { operation: 'leave' },
  label: { operation: 'leave' },
})

const messageFor = (error: unknown) => error instanceof ApiError
  ? error.displayMessage
  : error instanceof Error ? error.message : 'Bulk metadata operation failed.'

function currentState(tracks: TrackSummary[], field: InboxBulkMetadataField): string {
  if (!tracks.length) return 'Loading current values…'
  const values = new Set(tracks.map((track) => (track[field] ?? '').trim()).values())
  if (values.size > 1) return 'Mixed'
  return Array.from(values)[0] || 'Blank'
}

function operationDescription(preview: NonNullable<InboxBulkEditPreview['fields'][InboxBulkMetadataField]>) {
  if (preview.operation === 'clear') return `${preview.affected_count} will clear`
  if (preview.operation === 'append') return `${preview.affected_count} will append “${preview.value}”`
  return `${preview.affected_count} will change to “${preview.value}”`
}

export default function BulkMetadataEditor({ trackIds, tracks, onApplied, onClose }: Props) {
  const [operations, setOperations] = useState(initialOperations)
  const [preview, setPreview] = useState<InboxBulkEditPreview | null>(null)
  const [result, setResult] = useState<InboxBulkEditApplyResult | null>(null)
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const confirmButtonRef = useRef<HTMLButtonElement>(null)
  const reviewButtonRef = useRef<HTMLButtonElement>(null)
  const outcomeRef = useRef<HTMLDivElement>(null)

  const active = useMemo(() => Object.fromEntries(
    Object.entries(operations).filter(([, operation]) => operation.operation !== 'leave'),
  ) as Partial<Record<InboxBulkMetadataField, InboxBulkEditOperation>>, [operations])
  const invalid = Object.values(active).some((operation) => (
    (operation.operation === 'set' || operation.operation === 'append') && !operation.value?.trim()
  ))
  useEffect(() => { if (confirming) confirmButtonRef.current?.focus() }, [confirming])
  useEffect(() => { if (result) outcomeRef.current?.focus() }, [result])

  function cancelConfirmation() {
    setConfirming(false)
    window.setTimeout(() => reviewButtonRef.current?.focus(), 0)
  }

  function update(field: InboxBulkMetadataField, patch: Partial<InboxBulkEditOperation>) {
    setOperations((current) => ({ ...current, [field]: { ...current[field], ...patch } }))
    setPreview(null)
    setResult(null)
    setConfirming(false)
    setError(null)
  }

  async function runPreview() {
    setBusy('preview')
    setError(null)
    try {
      setPreview(await previewInboxBulkEdit(trackIds, active))
      setResult(null)
    } catch (previewError) {
      setError(messageFor(previewError))
    } finally {
      setBusy(null)
    }
  }

  async function apply() {
    setBusy('apply')
    setError(null)
    try {
      const next = await applyInboxBulkEdit(trackIds, active)
      setResult(next)
      setConfirming(false)
      await onApplied()
    } catch (applyError) {
      setError(messageFor(applyError))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="card settings-card inbox-bulk-edit" aria-labelledby="bulk-metadata-title">
      <div className="inbox-bulk-panel-head">
        <div>
          <h2 className="card-title" id="bulk-metadata-title"><Pencil size={16} /> Bulk metadata</h2>
          <p className="muted">Selected: {trackIds.length} track{trackIds.length === 1 ? '' : 's'}</p>
        </div>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>Close</button>
      </div>
      <p className="muted inbox-bulk-edit-note">Title, Artist, and Filename stay track-specific and cannot be edited in bulk.</p>

      <div className="inbox-bulk-edit-fields">
        {FIELDS.map((field) => {
          const operation = operations[field.id]
          const needsValue = operation.operation === 'set' || operation.operation === 'append'
          const valueMissing = needsValue && !operation.value?.trim()
          const currentId = `bulk-${field.id}-current`
          const errorId = `bulk-${field.id}-value-error`
          return (
            <div className="inbox-bulk-edit-field" key={field.id}>
              <div className="inbox-bulk-field-label">
                <label htmlFor={`bulk-${field.id}-operation`}>{field.label}</label>
                <span id={currentId}>Current: {currentState(tracks, field.id)}</span>
              </div>
              <select
                id={`bulk-${field.id}-operation`}
                className="form-input"
                value={operation.operation}
                disabled={busy !== null}
                aria-describedby={currentId}
                onChange={(event) => update(field.id, { operation: event.target.value as InboxBulkEditOperationName, value: undefined })}
              >
                {field.operations.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
              <input
                className="form-input"
                type="text"
                value={operation.value ?? ''}
                disabled={!needsValue || busy !== null}
                maxLength={field.id === 'comment' ? 1000 : 200}
                placeholder={needsValue ? field.placeholder : 'No value needed'}
                aria-label={`${field.label} bulk value`}
                aria-invalid={valueMissing}
                aria-describedby={valueMissing ? `${currentId} ${errorId}` : currentId}
                onChange={(event) => update(field.id, { value: event.target.value })}
              />
              {valueMissing && <span id={errorId} className="inbox-cell-error">Enter a value to use {operation.operation}.</span>}
            </div>
          )
        })}
      </div>

      {error && <StatusStrip tone="danger" onDismiss={() => setError(null)}>{error}</StatusStrip>}

      <div className="settings-actions inbox-bulk-primary-actions">
        <button type="button" className="btn btn--ghost btn--sm" disabled={!Object.keys(active).length || invalid || busy !== null || confirming} onClick={() => void runPreview()}>
          {busy === 'preview' ? <Loader2 size={13} className="spin" /> : null} Preview changes
        </button>
      </div>

      {preview && (
        <div className="inbox-bulk-edit-preview" aria-label="Bulk metadata preview">
          <h3>Preview changes</h3>
          <p className="inbox-bulk-edit-impact">
            {preview.selected_count} selected · {preview.changeable_count} affected · {preview.eligible_count - preview.changeable_count} unchanged across selected operations
            {preview.unsupported_count ? ` · ${preview.unsupported_count} unsupported` : ''}
            {preview.skipped_not_inbox ? ` · ${preview.skipped_not_inbox} outside Inbox` : ''}
            {preview.missing_count ? ` · ${preview.missing_count} not found` : ''}
          </p>
          <div className="inbox-bulk-preview-grid">
            {FIELDS.map((field) => {
              const item = preview.fields[field.id]
              if (!item) return null
              return (
                <div className="inbox-bulk-edit-preview-field" key={field.id}>
                  <strong>{field.label}</strong>
                  <span>{operationDescription(item)}</span>
                  <small>{item.already_matching_count} already match · {item.skipped_count} skipped</small>
                </div>
              )
            })}
          </div>
          {preview.items.some((item) => item.status === 'unsupported' || item.status === 'not_inbox' || item.status === 'not_found') && (
            <ul className="inbox-bulk-result-list" aria-label="Tracks that will be skipped">
              {preview.items.filter((item) => item.status === 'unsupported' || item.status === 'not_inbox' || item.status === 'not_found').map((item) => (
                <li key={item.track_id}>
                  <strong>{item.filename || `Track ${item.track_id}`}</strong>
                  <span>{item.status.replace('_', ' ')}</span>
                  <small>{item.reason || (item.status === 'not_inbox' ? 'Track is outside Inbox.' : 'Track is unavailable for this write.')}</small>
                </li>
              ))}
            </ul>
          )}
          <div className="settings-actions">
            <button ref={reviewButtonRef} type="button" className="btn btn--primary btn--sm" disabled={!preview.changeable_count || busy !== null} onClick={() => setConfirming(true)}>
              Review & apply
            </button>
          </div>
          {confirming && (
            <div className="inbox-bulk-confirm" role="group" aria-labelledby="bulk-confirm-title" aria-describedby="bulk-confirm-description">
              <h3 id="bulk-confirm-title">Apply metadata to {preview.changeable_count} track{preview.changeable_count === 1 ? '' : 's'}?</h3>
              <p id="bulk-confirm-description">CrateIQ will back up each supported file, write only these fields, re-read them, and report every track result.</p>
              <div className="settings-actions">
                <button ref={confirmButtonRef} type="button" className="btn btn--primary btn--sm" disabled={busy !== null} onClick={() => void apply()}>
                  {busy === 'apply' ? <Loader2 size={13} className="spin" /> : <Check size={13} />} Apply changes
                </button>
                <button type="button" className="btn btn--ghost btn--sm" disabled={busy !== null} onClick={cancelConfirmation}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}

      {result && (
        <div ref={outcomeRef} className="inbox-bulk-result" aria-label="Bulk metadata results" tabIndex={-1}>
          <StatusStrip tone={result.failed_count || result.skipped_count ? 'warn' : 'good'} icon={result.failed_count ? <AlertTriangle size={14} /> : <Check size={14} />}>
            {result.succeeded_count} written and verified · {result.unchanged_count} unchanged
            {result.skipped_count ? ` · ${result.skipped_count} skipped` : ''}
            {result.failed_count ? ` · ${result.failed_count} failed (working metadata remains unsaved)` : ''}
          </StatusStrip>
          {result.results.some((item) => item.status === 'failed' || item.status === 'skipped' || item.status === 'not_found') && (
            <ul className="inbox-bulk-result-list" aria-label="Tracks requiring attention">
              {result.results.filter((item) => item.status === 'failed' || item.status === 'skipped' || item.status === 'not_found').map((item) => (
                <li key={item.track_id}>
                  <strong>Track {item.track_id}</strong>
                  <span>{item.status.replace('_', ' ')}</span>
                  <small>{item.reason || 'No additional detail was returned.'}</small>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
