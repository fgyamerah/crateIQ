import { Link } from 'react-router-dom'
import Badge from '../ui/Badge'

const labels: Record<string, string> = {
  unreviewed: 'Unreviewed',
  reviewed: 'Reviewed',
  favorite: 'Favorite',
  maybe: 'Maybe',
  rejected: 'Rejected',
  needs_work: 'Needs work',
}

interface Props {
  trackId: number
  status?: string
  rating?: number | null
}

export default function ReviewStatusBadge({ trackId, status = 'unreviewed', rating }: Props) {
  const tone = status === 'favorite'
    ? 'succeeded'
    : status === 'rejected'
      ? 'failed'
      : status === 'maybe' || status === 'needs_work'
        ? 'pending'
        : 'info'
  const label = labels[status] || labels.unreviewed

  return (
    <Link
      className="review-status-link"
      to={`/music-review?track_id=${trackId}`}
      aria-label={`Open ${label.toLowerCase()} music review for track ${trackId}`}
      onClick={(event) => event.stopPropagation()}
    >
      <Badge tone={tone}>{label}{rating ? ` · ${rating}/5` : ''}</Badge>
    </Link>
  )
}
