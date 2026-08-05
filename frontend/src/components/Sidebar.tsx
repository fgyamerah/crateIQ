import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  Library,
  AlertTriangle,
  Sparkles,
  ClipboardList,
  FolderTree,
  Database,
  Eraser,
  BarChart3,
  Wrench,
  Activity,
  ListChecks,
  ListMusic,
  Music,
  Download,
  HardDrive,
  ChevronLeft,
  ChevronRight,
  Disc3,
  Heart,
  Settings,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { fetchReviewSummary } from '../api/insights'
import { fetchTrackIssues } from '../api/tracks'
import { fetchMetadataRepairSummary } from '../api/metadataRepair'
import { fetchBpmSummary } from '../api/analysis'
import { fetchLibraryQuality } from '../api/libraryQuality'

interface NavItem {
  to:    string
  label: string
  Icon:  LucideIcon
  end?:  boolean
  badgeKey?: keyof NavBadges
}

interface NavSection {
  title: string
  items: NavItem[]
}

/** Sidebar nav badge counts — each sourced from an existing read-only endpoint. */
interface NavBadges {
  issues:     number | null
  enrichment: number | null
  repair:     number | null
  bpm:        number | null
}

const EMPTY_BADGES: NavBadges = { issues: null, enrichment: null, repair: null, bpm: null }

const NAV: NavSection[] = [
  {
    title: 'Browse',
    items: [
      { to: '/',             label: 'Library',          Icon: Library,       end: true },
      { to: '/quality',      label: 'Quality',          Icon: BarChart3 },
      { to: '/issues',       label: 'Issues',           Icon: AlertTriangle, badgeKey: 'issues' },
      { to: '/enrichment',   label: 'Enrichment Queue', Icon: Sparkles,      badgeKey: 'enrichment' },
      { to: '/metadata-repair', label: 'Metadata Repair', Icon: Wrench,      badgeKey: 'repair' },
      { to: '/metadata-sanitation', label: 'Metadata Sanitation', Icon: Eraser },
      { to: '/bpm-review',   label: 'BPM Review',       Icon: Activity,      badgeKey: 'bpm' },
      { to: '/audit',        label: 'Audit',            Icon: ClipboardList },
      { to: '/folders',      label: 'Folders',          Icon: FolderTree },
    ],
  },
  {
    title: 'Operations',
    items: [
      { to: '/jobs',        label: 'Jobs',        Icon: ListChecks },
      { to: '/crates',      label: 'Manual Crates', Icon: ListMusic },
      { to: '/smart-crates', label: 'Smart Crates', Icon: Sparkles },
      { to: '/set-builder', label: 'Set Builder', Icon: Music },
      { to: '/exports',     label: 'Export',      Icon: Download },
      { to: '/sync',        label: 'SSD Sync',    Icon: HardDrive },
      { to: '/settings',    label: 'Settings',    Icon: Settings },
    ],
  },
  {
    title: 'Reconciliation',
    items: [
      { to: '/reconciliation', label: 'Ledger', Icon: Database },
    ],
  },
]

interface Props {
  collapsed: boolean
  onToggle:  () => void
}

/** Library Health mini-card, sourced from GET /api/library/quality (already used by the Quality page). */
function LibraryHealth() {
  const [health, setHealth] = useState<{ pct: number; degraded: boolean } | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchLibraryQuality()
      .then((data) => {
        if (cancelled) return
        const total = data.total_tracks || 0
        const clean = Math.max(0, total - data.issue_total)
        const pct = total > 0 ? Math.round((clean / total) * 100) : 100
        setHealth({ pct, degraded: data.issue_total > 0 })
      })
      .catch(() => {
        if (!cancelled) setHealth(null)
      })
    return () => { cancelled = true }
  }, [])

  if (!health) return null

  return (
    <div className="sidebar-health">
      <div className="sidebar-health-head">
        <span>Library Health</span>
        <span className={`sidebar-health-status ${health.degraded ? 'sidebar-health-status--degraded' : 'sidebar-health-status--good'}`}>
          {health.degraded ? 'Degraded' : 'Good'}
        </span>
      </div>
      <div className="sidebar-health-value">{health.pct}%</div>
      <span className="sidebar-health-sub">Tracks with no open issues</span>
    </div>
  )
}

export default function Sidebar({ collapsed, onToggle }: Props) {
  const [badges, setBadges] = useState<NavBadges>(EMPTY_BADGES)

  useEffect(() => {
    let cancelled = false
    Promise.allSettled([
      fetchTrackIssues(),
      fetchReviewSummary(),
      fetchMetadataRepairSummary(),
      fetchBpmSummary(),
    ]).then(([issuesRes, enrichRes, repairRes, bpmRes]) => {
      if (cancelled) return
      const issues = issuesRes.status === 'fulfilled'
        ? Object.values(issuesRes.value).reduce((sum, n) => sum + (n || 0), 0)
        : null
      const enrichment = enrichRes.status === 'fulfilled' ? enrichRes.value.pending_count : null
      const repair = repairRes.status === 'fulfilled' ? repairRes.value.pending_count : null
      const bpm = bpmRes.status === 'fulfilled' ? (bpmRes.value.by_status?.pending ?? null) : null
      setBadges({ issues, enrichment, repair, bpm })
    })
    return () => { cancelled = true }
  }, [])

  return (
    <nav className={`sidebar${collapsed ? ' sidebar--collapsed' : ''}`}>
      <div className="sidebar-brand">
        <span className="sidebar-brand-icon"><Disc3 size={15} strokeWidth={2} /></span>
        {!collapsed && <span className="sidebar-brand-name">crateIQ</span>}
        <button
          className="sidebar-collapse-btn"
          onClick={onToggle}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      </div>

      <div className="sidebar-sections">
        {NAV.map(({ title, items }) => (
          <section className="sidebar-section" key={title}>
            {!collapsed && <div className="sidebar-section-title">{title}</div>}
            <ul className="sidebar-nav">
              {items.map(({ to, label, Icon, end, badgeKey }) => {
                const count = badgeKey ? badges[badgeKey] : null
                return (
                  <li key={to}>
                    <NavLink
                      to={to}
                      end={end}
                      className={({ isActive }) =>
                        ['sidebar-link', isActive ? 'sidebar-link--active' : ''].join(' ').trim()
                      }
                      title={collapsed ? label : undefined}
                    >
                      <Icon size={15} className="sidebar-icon" strokeWidth={1.75} />
                      {!collapsed && <span className="sidebar-link-label">{label}</span>}
                      {!collapsed && count != null && count > 0 && (
                        <span className={`sidebar-badge${badgeKey === 'issues' ? ' sidebar-badge--warn' : ''}`}>
                          {count > 99 ? '99+' : count}
                        </span>
                      )}
                    </NavLink>
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
      </div>

      {!collapsed && <LibraryHealth />}

      {!collapsed ? (
        <div className="sidebar-footer">
          <span className="sidebar-footer-app"><Heart size={11} strokeWidth={2.5} /></span>
          <div className="sidebar-footer-copy">
            <strong>DJ CrateIQ</strong>
            <span>Local app · v0.1.0</span>
          </div>
        </div>
      ) : (
        <div className="sidebar-footer">v0.1.0</div>
      )}
    </nav>
  )
}
