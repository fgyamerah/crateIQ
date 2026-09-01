import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  Check,
  CircleAlert,
  Disc3,
  FolderOpen,
  LibraryBig,
  Loader2,
  Plus,
  RefreshCw,
  WifiOff,
  X,
} from 'lucide-react'
import { ApiError } from '../api/client'
import {
  activateRegisteredLibrary,
  fetchActivationStatus,
  fetchCurrentLibrary,
  fetchLibraryRegistry,
} from '../api/libraryLauncher'
import type {
  ActivationStatusResponse,
  CurrentLibraryResponse,
  LibraryRegistryResponse,
  RecentLibrary,
} from '../api/libraryLauncher'
import heroImage from '../assets/images/crateiq-library-launcher-hero.webp'

const MAX_RECENT_LIBRARIES = 4
const POLL_INTERVAL_MS = 1_000
const MAX_STATUS_POLLS = 60
const MAX_CURRENT_POLLS = 12

type DialogKind = 'browse' | 'create'
type FeedbackTone = 'info' | 'success' | 'warning' | 'error'

interface Feedback {
  tone: FeedbackTone
  message: string
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms)
    signal.addEventListener('abort', () => {
      window.clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }, { once: true })
  })
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function apiErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError) || typeof error.detail !== 'object' || error.detail === null) return null
  const detail = 'detail' in error.detail ? (error.detail as { detail: unknown }).detail : error.detail
  if (typeof detail !== 'object' || detail === null || !('code' in detail)) return null
  return typeof (detail as { code: unknown }).code === 'string'
    ? (detail as { code: string }).code
    : null
}

function activationStartFailure(error: unknown): string {
  switch (apiErrorCode(error)) {
    case 'activation_in_progress':
      return 'Another library switch is already in progress. Wait a moment, then try again.'
    case 'supervisor_fail_closed':
      return 'Library switching is paused because the previous attempt could not be verified safely. Check the local service before retrying.'
    case 'supervisor_unavailable':
    case 'supervisor_invalid_response':
      return 'Library switching is temporarily unavailable. Check the local service, then retry.'
    case 'unknown_library':
      return 'That library is no longer registered on this CrateIQ installation. Refresh the list and choose another library.'
    case 'unsafe_library':
      return 'That library is missing or no longer safe to open. Its files were not changed.'
    case 'registry_unavailable':
      return 'The recent-library list is unavailable. CrateIQ did not attempt to open the library.'
    default:
      return 'CrateIQ could not start the library switch. Check the local connection and try again.'
  }
}

function activationFailure(status: ActivationStatusResponse): string {
  if (status.activation_status === 'blocked') {
    const work = status.blocker?.count === 1 ? 'A task is' : 'One or more tasks are'
    return `CrateIQ kept the current library open because ${work} still in progress.`
  }
  if (status.activation_status === 'fail_closed') {
    return 'CrateIQ could not verify a safe library switch. Check the local service before retrying.'
  }
  return 'CrateIQ could not open that library. The current library was kept unchanged.'
}

function classificationLabel(classification: string): string {
  switch (classification) {
    case 'managed_workspace': return 'Managed workspace'
    case 'legacy_direct_library': return 'Legacy direct library'
    case 'empty_folder': return 'Empty folder'
    case 'external_music_folder': return 'External music folder'
    case 'missing': return 'Missing'
    case 'malformed_or_unsafe': return 'Unavailable'
    default: return 'Registered library'
  }
}

function formatLastOpened(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'Previously opened'
  return `Opened ${new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)}`
}

function EntryPointDialog({ kind, onClose }: { kind: DialogKind; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const isBrowse = kind === 'browse'

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    dialog.showModal()
    return () => dialog.close()
  }, [])

  return (
    <dialog
      ref={dialogRef}
      className="launcher-dialog"
      aria-labelledby={`launcher-${kind}-title`}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClose={onClose}
    >
      <div className="launcher-dialog-header">
        <span className="launcher-dialog-icon" aria-hidden="true">
          {isBrowse ? <FolderOpen size={21} /> : <Plus size={21} />}
        </span>
        <button className="launcher-dialog-close" type="button" onClick={onClose} aria-label="Close dialog">
          <X size={18} />
        </button>
      </div>
      <h2 id={`launcher-${kind}-title`}>
        {isBrowse ? 'Browse Libraries' : 'Create New Library'}
      </h2>
      <p>
        {isBrowse
          ? 'Browsing and registering another folder is not available from this launcher yet. For safety, CrateIQ does not accept an unrestricted library path here.'
          : 'Creating a managed library is not available in this checkpoint. No folders or files have been created.'}
      </p>
      <div className="launcher-dialog-note">
        <CircleAlert size={16} aria-hidden="true" />
        <span>{isBrowse ? 'Only libraries already registered on this installation can be opened.' : 'Library creation will arrive with a dedicated, reviewable backend workflow.'}</span>
      </div>
      <button className="btn btn--primary launcher-dialog-action" type="button" onClick={onClose} autoFocus>
        Got it
      </button>
    </dialog>
  )
}

export default function LibraryLauncher() {
  const navigate = useNavigate()
  const [registry, setRegistry] = useState<LibraryRegistryResponse | null>(null)
  const [current, setCurrent] = useState<CurrentLibraryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [activatingLibraryId, setActivatingLibraryId] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [dialog, setDialog] = useState<DialogKind | null>(null)
  const activationController = useRef<AbortController | null>(null)
  const activationId = useRef<string | null>(null)

  const loadLauncher = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setLoadError(false)
    try {
      const [nextRegistry, nextCurrent] = await Promise.all([
        fetchLibraryRegistry(signal),
        fetchCurrentLibrary(signal),
      ])
      setRegistry(nextRegistry)
      setCurrent(nextCurrent)
    } catch (error) {
      if (isAbortError(error)) return
      setLoadError(true)
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void loadLauncher(controller.signal)
    return () => controller.abort()
  }, [loadLauncher])

  useEffect(() => () => activationController.current?.abort(), [])

  const verifyCurrentLibrary = async (libraryId: string, signal: AbortSignal): Promise<boolean> => {
    for (let attempt = 0; attempt < MAX_CURRENT_POLLS; attempt += 1) {
      try {
        const nextCurrent = await fetchCurrentLibrary(signal)
        if (!nextCurrent.rootless && nextCurrent.library_id === libraryId) {
          setCurrent(nextCurrent)
          return true
        }
      } catch (error) {
        if (isAbortError(error)) throw error
      }
      await delay(POLL_INTERVAL_MS, signal)
    }
    return false
  }

  const selectLibrary = async (library: RecentLibrary) => {
    if (activatingLibraryId || !library.availability) return

    const controller = new AbortController()
    activationController.current?.abort()
    activationController.current = controller
    setActivatingLibraryId(library.library_id)
    activationId.current = null
    setFeedback({ tone: 'info', message: `Opening ${library.display_name}…` })

    try {
      const started = await activateRegisteredLibrary(library.library_id, controller.signal)
      activationId.current = started.activation_id

      for (let attempt = 0; attempt < MAX_STATUS_POLLS; attempt += 1) {
        await delay(POLL_INTERVAL_MS, controller.signal)
        let status: ActivationStatusResponse
        try {
          status = await fetchActivationStatus(controller.signal)
        } catch (error) {
          if (isAbortError(error)) throw error
          setFeedback({
            tone: 'info',
            message: `Reconnecting while ${library.display_name} opens…`,
          })
          continue
        }

        if (status.activation_id !== started.activation_id) {
          setFeedback({
            tone: 'info',
            message: `Waiting to verify ${library.display_name}…`,
          })
          continue
        }

        if (status.activation_status === 'succeeded') {
          setFeedback({ tone: 'success', message: `${library.display_name} is ready.` })
          const verified = await verifyCurrentLibrary(library.library_id, controller.signal)
          if (!verified) {
            setFeedback({
              tone: 'warning',
              message: 'The switch completed, but the current library could not be verified. Refresh before entering the workspace.',
            })
            return
          }
          await fetchLibraryRegistry(controller.signal).then(setRegistry).catch(() => undefined)
          navigate('/', { replace: true })
          return
        }

        if (['blocked', 'failed', 'fail_closed'].includes(status.activation_status)) {
          setFeedback({ tone: 'error', message: activationFailure(status) })
          return
        }
      }

      setFeedback({
        tone: 'warning',
        message: 'CrateIQ could not verify the library switch in time. Refresh the launcher before trying again.',
      })
    } catch (error) {
      if (isAbortError(error)) return
      setFeedback({ tone: 'error', message: activationStartFailure(error) })
    } finally {
      if (!controller.signal.aborted) setActivatingLibraryId(null)
      if (activationController.current === controller) activationController.current = null
    }
  }

  const recentLibraries = (registry?.recent_libraries ?? []).slice(0, MAX_RECENT_LIBRARIES)
  const launcherUnavailable = current?.launcher_status === 'supervisor_unavailable'

  return (
    <main className="launcher-page">
      <section className="launcher-hero" aria-labelledby="launcher-hero-title">
        <img src={heroImage} alt="" className="launcher-hero-image" />
        <div className="launcher-hero-shade" />
        <div className="launcher-brand"><Disc3 size={17} aria-hidden="true" /> <span>crateIQ</span></div>
        <div className="launcher-hero-copy">
          <h1 id="launcher-hero-title">Prepare every track. Own every set.</h1>
          <p>Your libraries stay local, deliberate, and ready for the next room.</p>
        </div>
      </section>

      <section className="launcher-content" aria-labelledby="launcher-title">
        <div className="launcher-content-inner">
          <header className="launcher-heading">
            <span className="launcher-heading-icon" aria-hidden="true"><LibraryBig size={19} /></span>
            <div>
              <h2 id="launcher-title">Choose your library</h2>
              <p>Open a recent CrateIQ workspace on this installation.</p>
            </div>
          </header>

          {loading ? (
            <div className="launcher-loading" role="status">
              <Loader2 className="spin" size={20} aria-hidden="true" />
              <span>Loading recent libraries…</span>
            </div>
          ) : loadError ? (
            <div className="launcher-state launcher-state--error" role="alert">
              <WifiOff size={21} aria-hidden="true" />
              <div><strong>Launcher unavailable</strong><p>Check the local CrateIQ service, then try again.</p></div>
              <button className="btn btn--ghost" type="button" onClick={() => void loadLauncher()}>
                <RefreshCw size={15} /> Retry
              </button>
            </div>
          ) : (
            <>
              <div className="launcher-current" aria-live="polite">
                <span className={`launcher-current-mark${current?.rootless ? '' : ' launcher-current-mark--active'}`} aria-hidden="true">
                  {current?.rootless ? <Disc3 size={16} /> : <Check size={16} />}
                </span>
                <div>
                  <span>Current library</span>
                  <strong>{current?.rootless ? 'No library open' : current?.display_name ?? 'Active library'}</strong>
                </div>
                <span className="launcher-current-state">
                  {launcherUnavailable ? 'Switching unavailable' : current?.rootless ? 'Choose one below' : 'Ready'}
                </span>
              </div>

              {launcherUnavailable && (
                <div className="launcher-feedback launcher-feedback--warning" role="status">
                  <CircleAlert size={17} aria-hidden="true" />
                  <span>Library switching is temporarily unavailable. Your current library is unchanged.</span>
                </div>
              )}

              {registry?.registry_status === 'malformed' && (
                <div className="launcher-feedback launcher-feedback--error" role="alert">
                  <CircleAlert size={17} aria-hidden="true" />
                  <span>The recent-library list could not be read safely. No library was opened.</span>
                </div>
              )}

              {feedback && (
                <div
                  className={`launcher-feedback launcher-feedback--${feedback.tone}`}
                  role={feedback.tone === 'error' ? 'alert' : 'status'}
                >
                  {feedback.tone === 'info' && activatingLibraryId
                    ? <Loader2 className="spin" size={17} aria-hidden="true" />
                    : feedback.tone === 'success'
                      ? <Check size={17} aria-hidden="true" />
                      : <CircleAlert size={17} aria-hidden="true" />}
                  <span>{feedback.message}</span>
                </div>
              )}

              <div className="launcher-list-heading">
                <h3>Recent libraries</h3>
                <span>{recentLibraries.length} of {MAX_RECENT_LIBRARIES}</span>
              </div>

              {recentLibraries.length > 0 ? (
                <div className="launcher-library-grid">
                  {recentLibraries.map((library) => {
                    const isActivating = activatingLibraryId === library.library_id
                    const disabled = Boolean(activatingLibraryId) || !library.availability || launcherUnavailable
                    return (
                      <button
                        className={`launcher-library-card${library.active ? ' launcher-library-card--active' : ''}${isActivating ? ' launcher-library-card--activating' : ''}`}
                        type="button"
                        key={library.library_id}
                        onClick={() => void selectLibrary(library)}
                        disabled={disabled}
                        aria-busy={isActivating}
                        aria-label={`${isActivating ? 'Opening ' : library.active ? 'Current library: ' : 'Open '}${library.display_name}${!library.availability ? ', unavailable' : ''}`}
                      >
                        <span className="launcher-card-topline">
                          <span className="launcher-card-icon" aria-hidden="true"><LibraryBig size={18} /></span>
                          <span className="launcher-card-status">
                            {isActivating ? <><Loader2 className="spin" size={13} /> Opening</>
                              : library.active ? <><Check size={13} /> Current</>
                                : !library.availability ? <><CircleAlert size={13} /> Unavailable</>
                                  : <><ArrowRight size={13} /> Open</>}
                          </span>
                        </span>
                        <strong>{library.display_name}</strong>
                        <span className="launcher-card-path" title={library.path}>{library.path}</span>
                        <span className="launcher-card-meta">
                          <span>{classificationLabel(library.classification)}</span>
                          <span>{formatLastOpened(library.last_opened_at)}</span>
                        </span>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <div className="launcher-empty">
                  <LibraryBig size={24} aria-hidden="true" />
                  <div><strong>No recent libraries</strong><p>This installation does not have a registered library to open yet.</p></div>
                </div>
              )}

              <div className="launcher-entry-actions">
                <button className="btn btn--ghost" type="button" onClick={() => setDialog('browse')}>
                  <FolderOpen size={17} /> Browse Libraries
                </button>
                <button className="btn btn--primary" type="button" onClick={() => setDialog('create')}>
                  <Plus size={17} /> Create New Library
                </button>
              </div>

              <p className="launcher-safety-note">Only registered library IDs can be opened. CrateIQ never sends a typed filesystem path from this screen.</p>
            </>
          )}
        </div>
      </section>

      {dialog && <EntryPointDialog kind={dialog} onClose={() => setDialog(null)} />}
    </main>
  )
}
