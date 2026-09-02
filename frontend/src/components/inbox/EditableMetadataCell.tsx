import { useEffect, useId, useRef, useState } from 'react'
import { Check, Pencil, X } from 'lucide-react'

interface Props {
  value: string
  ariaLabel: string
  onSave: (nextValue: string) => Promise<void>
  onEditingChange?: (editing: boolean) => void
  suffix?: string
  maxLength?: number
  variant?: 'table' | 'inspector'
}

function fieldName(label: string) {
  return label.replace(/^(Edit|New)\s+/i, '').replace(/\s+value$/i, '')
}

function validate(value: string, label: string, maxLength: number) {
  const name = fieldName(label)
  const normalized = value.normalize('NFC').trim()
  if (!normalized) return `${name} cannot be empty.`
  if (normalized.length > maxLength) return `${name} is too long (max ${maxLength} characters).`
  if (/[\u0000-\u001f]/.test(normalized)) return `${name} contains an unsafe control character.`
  return null
}

export default function EditableMetadataCell({
  value,
  ariaLabel,
  onSave,
  onEditingChange,
  suffix,
  maxLength = 200,
  variant = 'table',
}: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const inputId = useId()
  const isInspector = variant === 'inspector'

  useEffect(() => { if (!editing) setDraft(value) }, [value, editing])
  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  const setEditState = (next: boolean) => {
    setEditing(next)
    onEditingChange?.(next)
  }

  const startEdit = () => {
    setDraft(value)
    setLocalError(null)
    setEditState(true)
  }

  const cancel = () => {
    setDraft(value)
    setLocalError(null)
    setEditState(false)
  }

  const save = async () => {
    const normalized = draft.normalize('NFC').trim()
    const validationError = validate(draft, ariaLabel, maxLength)
    if (validationError) {
      setLocalError(validationError)
      return
    }
    if (normalized === value.normalize('NFC').trim()) {
      setEditState(false)
      return
    }
    setSaving(true)
    setLocalError(null)
    try {
      await onSave(normalized)
      setEditState(false)
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    const display = value || '—'
    if (isInspector) {
      return (
        <div className="inbox-inspector-edit-field">
          <span className="inbox-inspector-edit-label">{ariaLabel}</span>
          <button type="button" className="inbox-cell-edit-trigger" onClick={startEdit} aria-label={`Edit ${ariaLabel}`}>
            <span className="inbox-cell-value">{display}</span>
            <Pencil size={12} className="inbox-cell-pencil" aria-hidden="true" />
          </button>
        </div>
      )
    }
    return (
      <button type="button" className="inbox-cell-edit-trigger" onClick={startEdit} aria-label={`Edit ${ariaLabel}`}>
        <span className="inbox-cell-value">{display}</span>
        <Pencil size={12} className="inbox-cell-pencil" aria-hidden="true" />
      </button>
    )
  }

  const errorId = `${inputId}-error`
  const input = (
    <input
      ref={inputRef}
      id={inputId}
      className="inbox-cell-input"
      value={draft}
      maxLength={maxLength}
      disabled={saving}
      onChange={(event) => { setDraft(event.target.value); if (localError) setLocalError(null) }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') { event.preventDefault(); void save() }
        else if (event.key === 'Escape') { event.preventDefault(); cancel() }
      }}
      aria-label={`${ariaLabel} value`}
      aria-invalid={Boolean(localError)}
      aria-describedby={localError ? errorId : undefined}
    />
  )
  const controls = (
    <>
      {suffix && <span className="inbox-cell-suffix">{suffix}</span>}
      <button type="button" className="icon-btn icon-btn--sm icon-btn--approve" disabled={saving} onClick={() => void save()} aria-label={`Save ${ariaLabel}`}>
        <Check size={13} />
      </button>
      <button type="button" className="icon-btn icon-btn--sm" disabled={saving} onClick={cancel} aria-label={`Cancel editing ${ariaLabel}`}>
        <X size={13} />
      </button>
    </>
  )

  return (
    <span className={`inbox-cell-editing${isInspector ? ' inbox-cell-editing--inspector' : ''}`}>
      {isInspector && <label className="inbox-inspector-edit-label" htmlFor={inputId}>{ariaLabel}</label>}
      <span className="inbox-cell-input-row">
        {input}
        {controls}
      </span>
      {localError && <span id={errorId} className="inbox-cell-error" role="alert">{localError}</span>}
    </span>
  )
}
