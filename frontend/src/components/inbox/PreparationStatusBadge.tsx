import type { InboxPreparationState, InboxPreparationStatus } from '../../types/track'
import Badge from '../ui/Badge'

const tones: Record<InboxPreparationStatus, 'failed' | 'pending' | 'info' | 'running' | 'succeeded'> = {
  WRITE_BLOCKED: 'failed',
  NEEDS_ATTENTION: 'pending',
  REVIEW: 'info',
  UNSAVED: 'running',
  READY: 'succeeded',
}

export default function PreparationStatusBadge({
  state,
  idSuffix = '',
}: {
  state: InboxPreparationState | null | undefined
  idSuffix?: string
}) {
  if (!state) return <Badge tone="pending">Needs Attention</Badge>
  const descriptionId = `preparation-reasons-${state.track_id}${idSuffix}`
  const details = [
    ...state.reasons.map((reason) => reason.label),
    ...state.warnings.map((warning) => warning.label),
  ].join('. ')
  return (
    <span aria-describedby={descriptionId}>
      <Badge tone={tones[state.status]}>{state.status_label}</Badge>
      <span id={descriptionId} className="lib-visually-hidden">
        {details || 'No blockers or warnings'}
      </span>
    </span>
  )
}
