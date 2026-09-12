import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Loader2, X } from 'lucide-react'
import type { TrackSummary } from '../../types/track'
import { useTrackWaveform } from '../../hooks/useTrackWaveform'
import UnifiedWaveform from '../player/UnifiedWaveform'
import EditableMetadataCell from './EditableMetadataCell'
import PreparationStatusBadge from './PreparationStatusBadge'
import EnrichmentReviewPanel from './EnrichmentReviewPanel'
import type { InboxEditableMetadataField } from '../../types/track'
import RatingFavoriteControls from '../reviews/RatingFavoriteControls'

type InspectorTab = 'overview' | 'review' | 'status' | 'analysis' | 'file'

interface Props {
  track: TrackSummary | null
  loading: boolean
  onClose: () => void
  onPrevious?: () => void
  onNext?: () => void
  onMetadataSave?: (field: InboxEditableMetadataField, value: string) => Promise<void>
  onSaveToFile?: () => void
  onReviewDecision?: () => Promise<void> | void
  initialTab?: InspectorTab
  navigationLabel?: string
  onReviewChange?: (patch: { rating?: number | null; favorite?: boolean }) => Promise<void> | void
  onAddToPlaylist?: () => void
}

function value(value: string | number | null | undefined) {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function formatDuration(seconds: number | null) {
  if (seconds === null) return '—'
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.round(seconds % 60).toString().padStart(2, '0')
  return `${minutes}:${remainder}`
}

export default function InboxTrackInspector({ track, loading, onClose, onPrevious, onNext, onMetadataSave, onSaveToFile, onReviewDecision, initialTab = 'status', navigationLabel, onReviewChange, onAddToPlaylist }: Props) {
  const [tab, setTab] = useState<InspectorTab>(initialTab)
  const closeRef = useRef<HTMLButtonElement>(null)
  const waveform = useTrackWaveform(track?.id ?? null)
  const preparation = track?.preparation_state

  useEffect(() => {
    closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  useEffect(() => { setTab(initialTab) }, [initialTab, track?.id])

  const extension = track?.filename.includes('.') ? track.filename.split('.').pop()?.toUpperCase() : null
  const blocker = preparation?.reasons.find((reason) => reason.severity === 'blocker')?.label

  return (
    <aside className="inbox-inspector" role="dialog" aria-label="Inbox Track Inspector" aria-modal="false">
      <header className="inbox-inspector-header">
        <div>
          <span>Track Inspector</span>
          <strong>{loading ? 'Loading…' : value(track?.title || track?.filename)}</strong>
          <small>{value(track?.artist)}</small>
        </div>
        <button ref={closeRef} type="button" className="icon-btn" onClick={onClose} aria-label="Close Track Inspector">
          <X size={17} />
        </button>
      </header>

      <div className="inbox-inspector-nav" aria-label="Visible track navigation">
        <button type="button" className="btn btn--ghost btn--sm" onClick={onPrevious} disabled={!onPrevious}>
          <ChevronLeft size={14} /> Previous
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onNext} disabled={!onNext}>
          Next <ChevronRight size={14} />
        </button>
        {navigationLabel && <span className="inbox-inspector-queue-position">{navigationLabel}</span>}
      </div>

      <div className="inbox-inspector-signals">
        <span>Your signals</span>
        <RatingFavoriteControls
          rating={track?.rating}
          favorite={track?.favorite}
          compact
          disabled={!track || loading || !onReviewChange}
          onRatingChange={(rating) => onReviewChange?.({ rating })}
          onFavoriteChange={(favorite) => onReviewChange?.({ favorite })}
        />
      </div>
      {track && onAddToPlaylist && <button type="button" className="btn btn--ghost btn--sm inbox-inspector-add-playlist" onClick={onAddToPlaylist}>Add to Playlist</button>}

      <div className="inbox-inspector-tabs" role="tablist" aria-label="Track inspector sections">
        {(['overview', 'review', 'status', 'analysis', 'file'] as InspectorTab[]).map((item) => (
          <button
            key={item}
            id={`inbox-inspector-tab-${item}`}
            type="button"
            role="tab"
            aria-selected={tab === item}
            aria-controls={`inbox-inspector-panel-${item}`}
            className={tab === item ? 'is-active' : ''}
            onClick={() => setTab(item)}
          >
            {item === 'overview' ? 'Metadata' : item[0].toUpperCase() + item.slice(1)}
          </button>
        ))}
      </div>

      <div className={`inbox-inspector-body${tab === 'review' ? ' inbox-inspector-body--review' : ''}`}>
        {loading && <p className="muted"><Loader2 size={14} className="spin" /> Loading track details…</p>}
        {!loading && track && tab === 'overview' && (
          <section id="inbox-inspector-panel-overview" role="tabpanel" aria-labelledby="inbox-inspector-tab-overview">
            <h3>Overview / Metadata</h3>
            <div className="inbox-inspector-edit-fields">
              {(['artist', 'title', 'genre', 'album'] as InboxEditableMetadataField[]).map((field) => (
                <EditableMetadataCell
                  key={field}
                  value={track[field] ?? ''}
                  ariaLabel={field[0].toUpperCase() + field.slice(1)}
                  variant="inspector"
                  onSave={(next) => onMetadataSave ? onMetadataSave(field, next) : Promise.resolve()}
                />
              ))}
            </div>
            <dl className="inbox-inspector-defs">
              <dt>Filename</dt><dd>{track.filename}</dd>
              <dt>Duration</dt><dd>{formatDuration(track.duration_sec)}</dd>
              <dt>Bitrate</dt><dd>{track.bitrate_kbps ? `${track.bitrate_kbps} kbps` : '—'}</dd>
            </dl>
          </section>
        )}
        {!loading && track && tab === 'review' && (
          <section className="inbox-inspector-review-tab" id="inbox-inspector-panel-review" role="tabpanel" aria-labelledby="inbox-inspector-tab-review">
            <h3>Review suggestions</h3>
            <EnrichmentReviewPanel trackId={track.id} onDecision={onReviewDecision} />
          </section>
        )}
        {!loading && track && tab === 'status' && (
          <section id="inbox-inspector-panel-status" role="tabpanel" aria-labelledby="inbox-inspector-tab-status">
            <h3>Preparation status</h3>
            <div className="inbox-inspector-primary-state">
              <span>Primary state</span>
              <span>
                <PreparationStatusBadge state={preparation} idSuffix="-inspector" />
                {preparation?.status === 'UNSAVED' && <small className="inbox-inspector-unsaved-note">Changes not yet written to file</small>}
              </span>
            </div>
            <div className="inbox-inspector-save-action">
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={onSaveToFile}
                disabled={!preparation?.write.has_unsaved_changes || preparation.write.blocked || !onSaveToFile}
                title={preparation?.write.blocked ? 'This track cannot be written in its current format or state.' : undefined}
              >
                Save to File
              </button>
              {preparation?.write.has_unsaved_changes && preparation.write.blocked
                ? <small className="muted">Write blocked: resolve the current blocker before saving.</small>
                : preparation?.write.has_unsaved_changes
                  ? <small className="muted">Changes not yet written to file.</small>
                  : <small className="muted">No pending writable metadata changes.</small>}
            </div>
            <h4>Reasons</h4>
            {preparation?.reasons.length
              ? <ul>{preparation.reasons.map((reason) => <li key={reason.code}>{reason.label}</li>)}</ul>
              : <p className="muted">No blocking preparation reasons.</p>}
            <h4>Warnings</h4>
            {preparation?.warnings.length
              ? <ul>{preparation.warnings.map((warning) => <li key={warning.code}>{warning.label}</li>)}</ul>
              : <p className="muted">No current warnings.</p>}
            <dl className="inbox-inspector-defs">
              <dt>Pending fields</dt><dd>{preparation?.pending_fields.length ? preparation.pending_fields.join(', ') : 'None'}</dd>
              <dt>Review items</dt><dd>{preparation?.review_count ?? 0}</dd>
              <dt>Unsaved</dt><dd>{preparation?.write.has_unsaved_changes ? 'Yes' : 'No'}</dd>
              <dt>Write blocked</dt><dd>{preparation?.write.blocked ? 'Yes' : 'No'}</dd>
              <dt>Write blocker</dt><dd>{blocker ?? 'None'}</dd>
              <dt>Last write failure</dt><dd>{preparation?.write.last_failure ?? 'None'}</dd>
              <dt>Ready to promote</dt><dd>{preparation?.promotion.ready ? 'Yes' : 'No'}</dd>
              <dt>Destination</dt><dd>{preparation?.promotion.destination ?? 'Not available'}</dd>
              <dt>Collision</dt><dd>{preparation?.promotion.collision ?? 'None'}</dd>
            </dl>
          </section>
        )}
        {!loading && track && tab === 'analysis' && (
          <section id="inbox-inspector-panel-analysis" role="tabpanel" aria-labelledby="inbox-inspector-tab-analysis">
            <h3>Analysis</h3>
            <dl className="inbox-inspector-defs">
              <dt>BPM</dt><dd>{value(track.bpm)}</dd>
              <dt>Musical key</dt><dd>{value(track.key_musical)}</dd>
              <dt>Camelot key</dt><dd>{value(track.key_camelot)}</dd>
              <dt>Waveform</dt><dd>{waveform.loading ? 'Loading' : (waveform.waveform?.status ?? 'Not generated')}</dd>
            </dl>
            <div className="inbox-inspector-waveform">
              <UnifiedWaveform
                peaks={waveform.waveform?.status === 'ready' ? waveform.waveform.peaks : undefined}
                colorBands={waveform.waveform?.status === 'ready' ? waveform.waveform.colorBands : null}
                scale={waveform.waveform?.status === 'ready' ? waveform.waveform.scale : undefined}
                currentTime={0}
                duration={track.duration_sec ?? 0}
                inactive
                variant="standard"
                status={waveform.loading || !waveform.waveform ? 'loading' : waveform.waveform.status}
              />
            </div>
          </section>
        )}
        {!loading && track && tab === 'file' && (
          <section id="inbox-inspector-panel-file" role="tabpanel" aria-labelledby="inbox-inspector-tab-file">
            <h3>File</h3>
            <dl className="inbox-inspector-defs">
              <dt>Managed path</dt><dd className="inbox-inspector-path">{track.filepath}</dd>
              <dt>Filename</dt><dd>{track.filename}</dd>
              <dt>Format</dt><dd>{extension ?? '—'}</dd>
              <dt>Storage zone</dt><dd>{value(track.storage_zone)}</dd>
              <dt>Destination preview</dt><dd>{preparation?.promotion.destination ?? 'Not available'}</dd>
              <dt>Write capability</dt><dd>{preparation?.write.blocked ? `Blocked${blocker ? ` — ${blocker}` : ''}` : 'Available'}</dd>
              <dt>Collision</dt><dd>{preparation?.promotion.collision ?? 'None'}</dd>
            </dl>
          </section>
        )}
      </div>
    </aside>
  )
}
