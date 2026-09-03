import { useEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle2, CircleAlert, Loader2, Search, ShieldCheck, X } from 'lucide-react'
import { ApiError } from '../../api/client'
import { fetchMetadataSources } from '../../api/metadataSources'
import type { MetadataSource } from '../../types/metadataSources'

interface Props {
  trackCount: number
  onClose: () => void
  onConfirm: (sourceIds: string[]) => Promise<void>
}

function messageFor(error: unknown, fallback: string) {
  if (error instanceof ApiError) return error.displayMessage
  if (error instanceof Error && error.message) return error.message
  return fallback
}

function isEligible(source: MetadataSource) {
  return source.selectable_for_enrichment
    && source.role === 'track_enrichment'
    && source.enabled
    && source.configured
    && source.connection_status === 'ready'
}

export default function EnrichmentSourceDialog({ trackCount, onClose, onConfirm }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const [sources, setSources] = useState<MetadataSource[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const eligibleSources = useMemo(() => sources.filter(isEligible), [sources])

  useEffect(() => {
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeRef.current?.focus()
    return () => { returnFocusRef.current?.focus() }
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !submitting) {
        event.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose, submitting])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchMetadataSources()
      .then((response) => {
        if (cancelled) return
        const nextEligible = response.sources.filter(isEligible)
        setSources(response.sources)
        setSelectedIds(nextEligible.map((source) => source.id))
      })
      .catch((err) => {
        if (!cancelled) setError(messageFor(err, 'Could not load metadata sources from Settings.'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const toggleSource = (sourceId: string) => {
    setSelectedIds((current) => current.includes(sourceId)
      ? current.filter((id) => id !== sourceId)
      : [...current, sourceId])
  }

  const confirm = async () => {
    if (!selectedIds.length || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await onConfirm(selectedIds)
      onClose()
    } catch (err) {
      setError(messageFor(err, 'Could not start metadata enrichment. Refresh Settings and try again.'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="enrichment-source-backdrop" role="presentation">
      <section className="enrichment-source-dialog" role="dialog" aria-modal="true" aria-labelledby="enrichment-source-title" aria-describedby="enrichment-source-description">
        <header className="enrichment-source-header">
          <div>
            <span className="enrichment-source-kicker"><Search size={14} /> Batch metadata lookup</span>
            <h2 id="enrichment-source-title">Find metadata for {trackCount} selected track{trackCount === 1 ? '' : 's'}</h2>
          </div>
          <button ref={closeRef} type="button" className="icon-btn" onClick={onClose} disabled={submitting} aria-label="Close metadata source selection"><X size={17} /></button>
        </header>
        <div className="enrichment-source-body">
          <p id="enrichment-source-description" className="muted">Choose which ready enrichment sources may be queried for this Inbox batch. Credentials and global source settings stay in Settings.</p>
          <div className="enrichment-source-policy"><ShieldCheck size={15} /><span>Selected sources are eligible for this run. CrateIQ may stop early when it already has a strong match.</span></div>
          {loading && <p className="enrichment-source-loading"><Loader2 size={15} className="spin" /> Loading ready sources…</p>}
          {error && <p className="enrichment-source-error" role="alert" aria-live="assertive"><CircleAlert size={15} /> {error}</p>}
          {!loading && !error && !eligibleSources.length && (
            <div className="enrichment-source-empty" role="status">
              <strong>No sources available</strong>
              <p>Enable and configure at least one ready track-enrichment source in Settings, then reopen this selector.</p>
              <a href="/settings#metadata-sources" className="btn btn--ghost btn--sm">Open Settings</a>
            </div>
          )}
          {!loading && eligibleSources.length > 0 && (
            <fieldset className="enrichment-source-fieldset">
              <legend>Sources</legend>
              <div className="enrichment-source-list">
                {eligibleSources.map((source) => (
                  <label className="enrichment-source-option" key={source.id}>
                    <input
                      type="checkbox"
                      aria-label={source.label}
                      checked={selectedIds.includes(source.id)}
                      onChange={() => toggleSource(source.id)}
                      disabled={submitting}
                    />
                    <span className="enrichment-source-option-copy">
                      <strong>{source.label}</strong>
                      <small><CheckCircle2 size={13} /> Ready in Settings</small>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          <p className="enrichment-source-selected" aria-live="polite">{selectedIds.length} source{selectedIds.length === 1 ? '' : 's'} selected</p>
          <div className="enrichment-source-actions">
            <button type="button" className="btn btn--ghost" onClick={onClose} disabled={submitting}>Cancel</button>
            <button type="button" className="btn btn--primary" onClick={() => void confirm()} disabled={loading || submitting || !eligibleSources.length || !selectedIds.length}>
              {submitting ? 'Finding Metadata…' : 'Find Metadata'}
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
