import { Link } from 'react-router-dom'
import { AudioLines, ClipboardList, CopyCheck, Database, FolderTree } from 'lucide-react'
import PageHeader from '../components/PageHeader'

const AREAS = [
  {
    to: '/quality', icon: AudioLines, label: 'Quality',
    description: 'Audio quality findings: unreadable files, missing bitrate, unsupported formats, low bitrate. DB-only review, no transcode or file action.',
  },
  {
    to: '/duplicates', icon: CopyCheck, label: 'Duplicates',
    description: 'Duplicate track candidates with a recommended keeper. Review-only -- no file is ever deleted or moved from here.',
  },
  {
    to: '/reconciliation', icon: Database, label: 'Reconciliation',
    description: 'Review-first DB-only reconciliation for supported path references, with confirmation-gated rollback. No music-file move, rename, delete, quarantine, filesystem rollback, queue rewrite, or weak/ambiguous auto-repair.',
  },
  {
    to: '/folders', icon: FolderTree, label: 'Folders',
    description: 'Folder-level library view: track counts and issue density per folder.',
  },
  {
    to: '/audit', icon: ClipboardList, label: 'Audit',
    description: 'Latest path/library audit report.',
  },
]

export default function Maintenance() {
  return (
    <main className="page maintenance-page">
      <PageHeader
        title="Maintenance"
        subtitle="Library upkeep: quality, duplicates, reconciliation, folder structure, and audit history. Each area is its own review-first workspace."
      />
      <nav className="maintenance-grid" aria-label="Maintenance areas">
        {AREAS.map((area) => (
          <Link key={area.to} to={area.to} className="card settings-card maintenance-card">
            <h2 className="card-title"><area.icon size={16} /> {area.label}</h2>
            <p className="muted">{area.description}</p>
          </Link>
        ))}
      </nav>
    </main>
  )
}
