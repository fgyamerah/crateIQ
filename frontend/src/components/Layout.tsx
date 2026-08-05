import { useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import ReadinessBanner from './ReadinessBanner'
import PersistentBottomPlayer from './player/PersistentBottomPlayer'
import { usePersistentPlayer } from './player/usePersistentPlayer'

// The Library route ("/") renders its own compact LibraryRuntimeStrip
// instead of this full-width banner — see components/library/LibraryView.tsx
// and LibraryRuntimeStrip.tsx. Suppressing it here avoids showing the same
// readiness diagnostic twice, once huge and once compact.
const ROUTES_WITH_OWN_RUNTIME_STRIP = new Set(['/'])

export default function Layout() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const location = useLocation()
  const { currentTrack } = usePersistentPlayer()
  const showGlobalReadinessBanner = !ROUTES_WITH_OWN_RUNTIME_STRIP.has(location.pathname)

  return (
    <div className={`app-shell${sidebarCollapsed ? ' app-shell--sidebar-collapsed' : ''}${currentTrack ? ' app-shell--player-active' : ''}`}>
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((c) => !c)}
      />
      <main className="app-main">
        {showGlobalReadinessBanner && <ReadinessBanner />}
        <Outlet />
      </main>
      <PersistentBottomPlayer />
    </div>
  )
}
