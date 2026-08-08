import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  Library,
  AlertTriangle,
  ListChecks,
  ListMusic,
  Music,
  Rocket,
  ChevronLeft,
  ChevronRight,
  Disc3,
  Heart,
  Settings,
  Wrench,
  Inbox as InboxIcon,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { fetchNeedsReview } from '../api/needsReview'
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
  needsReview: number | null
}

const EMPTY_BADGES: NavBadges = { needsReview: null }

/**
 * Task-oriented top-level navigation. Every other specialist workflow
 * (Beets Review, Enrichment Review, Metadata Repair/Sanitation, Genre
 * Taxonomy, BPM Review, Quality Review, Apply to Files, Smart Crates,
 * Export, SSD Sync, Music Review) is still fully mounted and reachable --
 * via Needs Review's deep links, Maintenance's tabs, or direct URL -- it
 * is deliberately not repeated here as a permanent sidebar destination.
 */
const NAV: NavSection[] = [
  {
    title: 'Library',
    items: [
      { to: '/inbox',        label: 'Inbox',            Icon: InboxIcon },
      { to: '/',             label: 'Library',          Icon: Library,       end: true },
      { to: '/needs-review', label: 'Needs Review',     Icon: AlertTriangle, badgeKey: 'needsReview' },
    ],
  },
  {
    title: 'DJ',
    items: [
      { to: '/crates',      label: 'Crates',      Icon: ListMusic },
      { to: '/set-builder', label: 'Set Builder', Icon: Music },
      { to: '/publish',     label: 'Publish',     Icon: Rocket },
    ],
  },
  {
    title: 'Tools',
    items: [
      { to: '/jobs',        label: 'Jobs',        Icon: ListChecks },
      { to: '/maintenance', label: 'Maintenance', Icon: Wrench },
    ],
  },
  {
    title: 'System',
    items: [
      { to: '/settings',    label: 'Settings',    Icon: Settings },
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
    fetchNeedsReview('ALL')
      .then((result) => {
        if (!cancelled) setBadges({ needsReview: result.counts.ALL ?? null })
      })
      .catch(() => {
        if (!cancelled) setBadges(EMPTY_BADGES)
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
                        <span className={`sidebar-badge${badgeKey === 'needsReview' ? ' sidebar-badge--warn' : ''}`}>
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
