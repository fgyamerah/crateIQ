import { useEffect, useState } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { fetchCurrentLibrary } from '../api/libraryLauncher'

type GateState = 'checking' | 'ready' | 'launcher'

export default function LibraryRequirement() {
  const [state, setState] = useState<GateState>('checking')
  const location = useLocation()

  useEffect(() => {
    const controller = new AbortController()
    fetchCurrentLibrary(controller.signal)
      .then((current) => setState(current.rootless ? 'launcher' : 'ready'))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setState('launcher')
      })
    return () => controller.abort()
  }, [])

  if (state === 'checking') {
    return (
      <main className="library-gate" aria-live="polite">
        <span className="library-gate-spinner" aria-hidden="true" />
        <p>Checking the current library…</p>
      </main>
    )
  }

  if (state === 'launcher') {
    return <Navigate to="/libraries" replace state={{ from: location.pathname }} />
  }

  return <Outlet />
}
