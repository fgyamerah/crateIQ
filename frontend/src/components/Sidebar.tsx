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
  Music,
  Download,
  HardDrive,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

interface NavItem {
  to:    string
  label: string
  Icon:  LucideIcon
  end?:  boolean
}

interface NavSection {
  title: string
  items: NavItem[]
}

const NAV: NavSection[] = [
  {
    title: 'Browse',
    items: [
      { to: '/',             label: 'Library',          Icon: Library,       end: true },
      { to: '/quality',      label: 'Quality',          Icon: BarChart3 },
      { to: '/issues',       label: 'Issues',           Icon: AlertTriangle },
      { to: '/enrichment',   label: 'Enrichment Queue', Icon: Sparkles },
      { to: '/metadata-repair', label: 'Metadata Repair', Icon: Wrench },
      { to: '/metadata-sanitation', label: 'Metadata Sanitation', Icon: Eraser },
      { to: '/bpm-review',   label: 'BPM Review',       Icon: Activity },
      { to: '/audit',        label: 'Audit',            Icon: ClipboardList },
      { to: '/folders',      label: 'Folders',          Icon: FolderTree },
    ],
  },
  {
    title: 'Operations',
    items: [
      { to: '/jobs',        label: 'Jobs',        Icon: ListChecks },
      { to: '/set-builder', label: 'Set Builder', Icon: Music },
      { to: '/exports',     label: 'Export',      Icon: Download },
      { to: '/sync',        label: 'SSD Sync',    Icon: HardDrive },
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

export default function Sidebar({ collapsed, onToggle }: Props) {
  return (
    <nav className={`sidebar${collapsed ? ' sidebar--collapsed' : ''}`}>
      <div className="sidebar-brand">
        {!collapsed && (
          <>
            <span className="sidebar-brand-icon">▶</span>
            <span className="sidebar-brand-name">CrateIQ</span>
          </>
        )}
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
              {items.map(({ to, label, Icon, end }) => (
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
                  </NavLink>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>

      {!collapsed && <div className="sidebar-footer">v0.1.0</div>}
    </nav>
  )
}
