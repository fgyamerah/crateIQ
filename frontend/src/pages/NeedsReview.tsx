import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchNeedsReview } from '../api/needsReview'
import type { NeedsReviewCategory, NeedsReviewItem, NeedsReviewResponse } from '../api/needsReview'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'

function messageFor(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.displayMessage : fallback
}

const TABS: { value: NeedsReviewCategory; label: string }[] = [
  { value: 'ALL', label: 'All' },
  { value: 'METADATA', label: 'Metadata' },
  { value: 'IDENTITY_ENRICHMENT', label: 'Identity & Enrichment' },
  { value: 'GENRE', label: 'Genre' },
  { value: 'ANALYSIS', label: 'Analysis' },
  { value: 'QUALITY', label: 'Quality' },
]

function severityTone(severity: NeedsReviewItem['severity']) {
  if (severity === 'HIGH') return 'failed' as const
  if (severity === 'MEDIUM') return 'pending' as const
  return 'info' as const
}

export default function NeedsReview() {
  const [category, setCategory] = useState<NeedsReviewCategory>('ALL')
  const [data, setData] = useState<NeedsReviewResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (cat: NeedsReviewCategory) => {
    setLoading(true)
    setError(null)
    try {
      setData(await fetchNeedsReview(cat))
    } catch (err) {
      setError(messageFor(err, 'Could not load Needs Review.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load(category) }, [category, load])

  return (
    <main className="page needs-review-page">
      <PageHeader
        title="Needs Review"
        subtitle="Read-only aggregation of exceptions across metadata, identity, genre, analysis, and quality. Resolve each item on its specialist page."
        actions={
          <button className="btn btn--ghost btn--sm" disabled={loading} onClick={() => void load(category)}>
            <RefreshCw size={14} /> Refresh
          </button>
        }
      />

      {error && <StatusStrip tone="danger" onDismiss={() => setError(null)}>{error}</StatusStrip>}

      <div className="reconciliation-tabs" role="tablist" aria-label="Needs Review categories">
        {TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            role="tab"
            aria-selected={category === tab.value}
            className={`reconciliation-tab${category === tab.value ? ' is-active' : ''}`}
            onClick={() => setCategory(tab.value)}
          >
            {tab.label}
            {data && <span className="badge-count">{data.counts[tab.value] ?? 0}</span>}
          </button>
        ))}
      </div>

      {!data?.items.length && !loading ? (
        <EmptyState
          icon={<AlertTriangle size={22} />}
          title="Nothing needs review"
          message="No open items in this category. Import and process Inbox tracks, or refresh the specialist queues (Metadata Repair, Enrichment Review, Quality Review) to populate this view."
        />
      ) : (
        <div className="card settings-card table-scroll">
          <table>
            <thead>
              <tr>
                <th>Track</th><th>Category</th><th>Severity</th><th>Issue</th><th>Provenance</th><th>Resolve</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((item, index) => (
                <tr key={`${item.track_id}-${item.reason_code}-${index}`}>
                  <td>{item.filename || `Track #${item.track_id}`}</td>
                  <td>{item.category.replace('_', ' ')}</td>
                  <td><Badge tone={severityTone(item.severity)}>{item.severity}</Badge></td>
                  <td>{item.summary}</td>
                  <td className="muted">{item.provenance || '—'}</td>
                  <td>
                    {item.actions.map((action) => (
                      <Link key={action.route} className="btn btn--ghost btn--xs" to={action.route}>
                        {action.label}
                      </Link>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  )
}
