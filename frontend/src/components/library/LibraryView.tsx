import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertOctagon, CheckCircle2, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react'
import { fetchLibraryOverview } from '../../api/library'
import { fetchReviewSummary } from '../../api/reviews'
import type { ReviewSummary } from '../../api/reviews'
import type { LibraryOverview } from '../../api/library'
import { fetchTrack, fetchTrackIssues, fetchTrackPage } from '../../api/tracks'
import type {
  TrackDetail,
  TrackIssueCounts,
  TrackListParams,
  TrackPage,
} from '../../types/track'
import LibraryRuntimeStrip from './LibraryRuntimeStrip'
import LibraryToolbar from './LibraryToolbar'
import LibraryOverviewCards from './LibraryOverview'
import LibraryFilters from './LibraryFilters'
import TrackTable from './TrackTable'
import TrackInspector from './TrackInspector'
import { usePersistentPlayer } from '../player/usePersistentPlayer'
import type { PersistentPlayerTrack } from '../player/usePersistentPlayer'
import type { LibraryUiState, SortKey, SortOrder } from './libraryUtils'
import { LIMIT, loadUiState, persistUiState } from './libraryUtils'

function LibraryStatusStrip({
  overview,
  issueTotal,
  loading,
  lastRefreshed,
  collapsed,
  onToggleCollapsed,
  onReviewIssues,
  onRescan,
}: {
  overview: LibraryOverview | null
  issueTotal: number
  loading: boolean
  lastRefreshed: Date | null
  collapsed: boolean
  onToggleCollapsed: () => void
  onReviewIssues: () => void
  onRescan: () => void
}) {
  const total = overview?.total_tracks ?? 0
  const degraded = issueTotal > 0
  const statusLabel = !overview ? 'Unknown' : degraded ? 'Degraded' : 'Good'
  return (
    <div className={`lib-status-strip${degraded ? ' lib-status-strip--degraded' : ' lib-status-strip--good'}`} role="status">
      <div className="lib-status-strip-main">
        <span className="lib-status-dot" aria-hidden="true">
          {degraded ? <AlertOctagon size={14} /> : <CheckCircle2 size={14} />}
        </span>
        <strong>Library status: {statusLabel}</strong>
        {!collapsed && (
          <>
            <span className="lib-status-strip-sep">·</span>
            <span>{total.toLocaleString()} tracks scanned</span>
            {degraded && (
              <>
                <span className="lib-status-strip-sep">·</span>
                <span>Some metadata issues detected</span>
                <button type="button" className="lib-status-strip-link" onClick={onReviewIssues}>
                  Review issues →
                </button>
              </>
            )}
          </>
        )}
      </div>
      <div className="lib-status-strip-meta">
        <span>{lastRefreshed ? `Last scan: ${lastRefreshed.toLocaleTimeString()}` : 'Not yet scanned'}</span>
        <button type="button" className="lib-btn lib-btn--ghost lib-btn--sm" onClick={onRescan} disabled={loading}>
          <RefreshCw size={12} className={loading ? 'spin' : undefined} />
          Re-scan
        </button>
        <button
          type="button"
          className="lib-status-strip-collapse"
          onClick={onToggleCollapsed}
          title={collapsed ? 'Expand status strip' : 'Collapse status strip'}
        >
          {collapsed ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
        </button>
      </div>
    </div>
  )
}

export default function LibraryView() {
  const navigate = useNavigate()
  const persistentPlayer = usePersistentPlayer()
  const [ui, setUi] = useState<LibraryUiState>(() => loadUiState())
  const searchRef = useRef<HTMLInputElement>(null)
  const moreMenuRef = useRef<HTMLDivElement>(null)

  const [overview, setOverview] = useState<LibraryOverview | null>(null)
  const [issues, setIssues] = useState<TrackIssueCounts | null>(null)
  const [trackPage, setTrackPage] = useState<TrackPage | null>(null)
  const [selectedDetail, setSelectedDetail] = useState<TrackDetail | null>(null)
  const [reviewSummary, setReviewSummary] = useState<ReviewSummary>({})
  const [detailLoading, setDetailLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)
  const [moreMenuOpen, setMoreMenuOpen] = useState(false)

  const setUiPatch = useCallback((updater: (current: LibraryUiState) => LibraryUiState) => {
    setUi((current) => updater(current))
  }, [])

  // Debounce free-text search into the applied filter.
  useEffect(() => {
    const id = window.setTimeout(() => {
      setUi((current) => ({ ...current, search: current.searchDraft, offset: 0 }))
    }, 350)
    return () => window.clearTimeout(id)
  }, [ui.searchDraft])

  useEffect(() => {
    persistUiState(ui)
  }, [ui])

  // Cmd/Ctrl+K focuses the search field.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Close the "more" menu on outside click.
  useEffect(() => {
    if (!moreMenuOpen) return
    function onClick(e: MouseEvent) {
      if (moreMenuRef.current && !moreMenuRef.current.contains(e.target as Node)) {
        setMoreMenuOpen(false)
      }
    }
    window.addEventListener('mousedown', onClick)
    return () => window.removeEventListener('mousedown', onClick)
  }, [moreMenuOpen])

  const params: TrackListParams = useMemo(() => ({
    search: ui.search || undefined,
    genre: ui.genreFilter || undefined,
    bpm_min: ui.bpmMinFilter ? Number(ui.bpmMinFilter) : undefined,
    bpm_max: ui.bpmMaxFilter ? Number(ui.bpmMaxFilter) : undefined,
    has_key: ui.hasKeyFilter ? ui.hasKeyFilter === 'yes' : undefined,
    sort: ui.sort,
    order: ui.order,
    limit: LIMIT,
    offset: ui.offset,
  }), [ui.search, ui.genreFilter, ui.bpmMinFilter, ui.bpmMaxFilter, ui.hasKeyFilter, ui.sort, ui.order, ui.offset])

  const loadMain = useCallback(async () => {
    setLoading(true)
    try {
      const [overviewData, issueData, pageData] = await Promise.all([
        fetchLibraryOverview(),
        fetchTrackIssues(),
        fetchTrackPage(params),
      ])
      setOverview(overviewData)
      setIssues(issueData)
      setTrackPage(pageData)
      setError(null)
      setLastRefreshed(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load library data')
    } finally {
      setLoading(false)
    }
  }, [params])

  useEffect(() => {
    loadMain()
  }, [loadMain])

  // Auto-select the first visible track whenever a fresh page loads and
  // nothing is selected yet, so the inspector is populated immediately
  // instead of waiting for a manual click.
  useEffect(() => {
    if (ui.selectedId != null) return
    const firstId = trackPage?.items?.[0]?.id
    if (firstId != null) {
      setUi((current) => (current.selectedId == null ? { ...current, selectedId: firstId } : current))
    }
  }, [trackPage, ui.selectedId])

  useEffect(() => {
    if (!ui.selectedId) {
      setSelectedDetail(null)
      return
    }
    setDetailLoading(true)
    fetchTrack(ui.selectedId)
      .then(setSelectedDetail)
      .catch((e) => {
        if (typeof e === 'object' && e !== null && 'status' in e && (e as { status?: number }).status === 404) {
          setSelectedDetail(null)
          setUi((current) => ({ ...current, selectedId: null }))
          return
        }
        setError(e instanceof Error ? e.message : 'Failed to load track detail')
      })
      .finally(() => setDetailLoading(false))
  }, [ui.selectedId])

  function refresh() {
    loadMain()
    setMoreMenuOpen(false)
  }

  function handleSort(nextSort: SortKey, nextOrder?: SortOrder) {
    setUi((current) => {
      if (nextOrder) return { ...current, sort: nextSort, order: nextOrder, offset: 0 }
      const order: SortOrder = current.sort === nextSort ? (current.order === 'asc' ? 'desc' : 'asc') : 'asc'
      return { ...current, sort: nextSort, order, offset: 0 }
    })
  }

  const items = trackPage?.items ?? []
  const playerQueue = useMemo<PersistentPlayerTrack[]>(() => items.map((track) => ({
    id: track.id,
    artist: track.artist,
    title: track.title,
    filename: track.filename,
    genre: track.genre,
    bpm: track.bpm,
    key_camelot: track.key_camelot,
    duration_sec: track.duration_sec,
    relativePath: track.filepath,
    sourceLabel: 'Library',
  })), [items])

  useEffect(() => {
    const playerTrack = persistentPlayer.currentTrack
    if (!playerTrack || playerTrack.sourceLabel !== 'Library') return
    if (!items.some((track) => track.id === playerTrack.id)) return
    setUi((current) => current.selectedId === playerTrack.id
      ? current
      : { ...current, selectedId: playerTrack.id })
  }, [items, persistentPlayer.currentTrack])

  const total = trackPage?.total ?? 0
  const issueTotal = issues ? Object.values(issues).reduce((sum, n) => sum + (n || 0), 0) : 0
  const activeFilterCount = [ui.genreFilter, ui.bpmMinFilter, ui.bpmMaxFilter, ui.hasKeyFilter].filter(Boolean).length

  useEffect(() => {
    let cancelled = false
    fetchReviewSummary(items.map((track) => track.id))
      .then((response) => {
        if (!cancelled) setReviewSummary(response.reviews)
      })
      .catch(() => {
        if (!cancelled) setReviewSummary({})
      })
    return () => { cancelled = true }
  }, [trackPage])

  const selectTrack = (id: number) => {
    setUi((current) => ({ ...current, selectedId: id }))
    const track = playerQueue.find((item) => item.id === id)
    if (track) persistentPlayer.loadTrack(track, playerQueue)
  }

  const playTrack = (id: number) => {
    const track = playerQueue.find((item) => item.id === id)
    if (!track) return
    if (persistentPlayer.currentTrack?.id === id) {
      void persistentPlayer.togglePlayback()
      return
    }
    persistentPlayer.loadTrack(track, playerQueue, { autoplay: true })
  }

  return (
    <div className="lib-workspace">
      <LibraryToolbar
        searchDraft={ui.searchDraft}
        searchRef={searchRef}
        onSearchChange={(value) => setUi((current) => ({ ...current, searchDraft: value }))}
        activeFilterCount={activeFilterCount}
        onToggleFilters={() => setUi((current) => ({ ...current, filtersExpanded: !current.filtersExpanded }))}
        sort={ui.sort}
        order={ui.order}
        onSortChange={(sort, order) => handleSort(sort, order)}
        density={ui.density}
        onToggleDensity={() => setUi((current) => ({ ...current, density: current.density === 'compact' ? 'comfortable' : 'compact' }))}
        moreMenuOpen={moreMenuOpen}
        moreMenuRef={moreMenuRef}
        onToggleMore={() => setMoreMenuOpen((v) => !v)}
        onRefresh={refresh}
        onOpenAnalysisTools={() => navigate('/settings#analysis-tools')}
      />

      <LibraryRuntimeStrip />

      {error && <div className="error-banner lib-error">{error}</div>}

      <div className="lib-body">
        <LibraryStatusStrip
          overview={overview}
          issueTotal={issueTotal}
          loading={loading}
          lastRefreshed={lastRefreshed}
          collapsed={ui.statusStripCollapsed}
          onToggleCollapsed={() => setUi((current) => ({ ...current, statusStripCollapsed: !current.statusStripCollapsed }))}
          onReviewIssues={() => navigate('/issues')}
          onRescan={refresh}
        />

        <LibraryOverviewCards overview={overview} />

        <LibraryFilters
          overview={overview}
          ui={ui}
          expanded={ui.filtersExpanded}
          onChange={setUiPatch}
          onToggleExpanded={() => setUi((current) => ({ ...current, filtersExpanded: !current.filtersExpanded }))}
        />

        <div className="lib-content">
          <TrackTable
            items={items}
            total={total}
            loading={loading}
            offset={ui.offset}
            selectedId={ui.selectedId}
            sort={ui.sort}
            order={ui.order}
            density={ui.density}
            reviews={reviewSummary}
            playingTrackId={persistentPlayer.playing ? persistentPlayer.currentTrack?.id ?? null : null}
            onSort={(key) => handleSort(key)}
            onSelect={selectTrack}
            onPlay={playTrack}
            onPrevPage={() => setUi((current) => ({ ...current, offset: Math.max(0, current.offset - LIMIT) }))}
            onNextPage={() => setUi((current) => ({ ...current, offset: current.offset + LIMIT }))}
            onOpenImportWizard={() => navigate('/settings#library-setup-import')}
          />

          <TrackInspector
            track={selectedDetail}
            loading={detailLoading}
            isCurrentTrack={Boolean(selectedDetail && persistentPlayer.currentTrack?.id === selectedDetail.id)}
            isPlaying={persistentPlayer.playing && persistentPlayer.currentTrack?.id === selectedDetail?.id}
            onPlay={() => selectedDetail && playTrack(selectedDetail.id)}
          />
        </div>
      </div>
    </div>
  )
}
