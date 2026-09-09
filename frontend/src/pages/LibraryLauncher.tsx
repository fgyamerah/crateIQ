import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Disc3,
  Folder,
  FolderOpen,
  LibraryBig,
  Loader2,
  LockKeyhole,
  Plus,
  RefreshCw,
  WifiOff,
  X,
} from 'lucide-react'
import { ApiError } from '../api/client'
import {
  activateRegisteredLibrary,
  createLibrary,
  fetchActivationStatus,
  fetchBrowseDirectories,
  fetchCurrentLibrary,
  fetchLibraryRegistry,
  registerExistingLibrary,
} from '../api/libraryLauncher'
import type {
  ActivationStatusResponse,
  BrowseEntry,
  BrowseResponse,
  CurrentLibraryResponse,
  LibraryRegistryResponse,
  RecentLibrary,
  RegisteredLibraryResponse,
} from '../api/libraryLauncher'
import heroImage from '../assets/images/crateiq-library-launcher-hero.webp'

const MAX_RECENT_LIBRARIES = 4
const POLL_INTERVAL_MS = 1_000
const MAX_STATUS_POLLS = 60
const MAX_CURRENT_POLLS = 12
const BROWSE_PAGE_SIZE = 50
const MAX_LIBRARY_NAME_LENGTH = 120

type DialogKind = 'browse' | 'create'
type FeedbackTone = 'info' | 'success' | 'warning' | 'error'
type LocalAdminStatus = 'checking' | 'available' | 'unavailable' | 'error'

interface Feedback {
  tone: FeedbackTone
  message: string
}

interface ActivationTarget {
  library_id: string
  display_name: string
}

interface DialogState {
  kind: DialogKind
  startPath?: string
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

function apiErrorPayload(error: unknown): Record<string, unknown> | null {
  if (!(error instanceof ApiError) || typeof error.detail !== 'object' || error.detail === null) return null
  const detail = 'detail' in error.detail ? (error.detail as { detail: unknown }).detail : error.detail
  return typeof detail === 'object' && detail !== null ? detail as Record<string, unknown> : null
}

function apiErrorCode(error: unknown): string | null {
  const detail = apiErrorPayload(error)
  return detail && typeof detail.code === 'string' ? detail.code : null
}

function isLocalOnlyDenial(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403
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
    case 'unsafe': return 'Unavailable'
    case 'missing': return 'Missing'
    case 'malformed_or_unsafe': return 'Unavailable'
    default: return 'Registered library'
  }
}

function browseFailure(error: unknown): string {
  if (isLocalOnlyDenial(error)) {
    return 'Browsing is available only from CrateIQ on the host machine.'
  }
  if (apiErrorCode(error) === 'invalid_browse_location') {
    return 'That location is unavailable, inaccessible, or outside the allowed folders.'
  }
  return 'CrateIQ could not load folders. Check the local connection and try again.'
}

function registerFailure(error: unknown): string {
  if (isLocalOnlyDenial(error)) return 'Registration is available only from CrateIQ on the host machine.'
  switch (apiErrorCode(error)) {
    case 'unsafe_path':
      return 'That directory is unavailable or outside the allowed folders.'
    case 'not_a_valid_library':
      return 'That directory is no longer a valid CrateIQ library. Refresh the folder and try again.'
    case 'registry_unavailable':
      return 'The library registry is unavailable. No library was opened.'
    case 'registry_write_failed':
      return 'CrateIQ could not add the library to the launcher. Try again after checking local storage.'
    default:
      return 'CrateIQ could not register that library. No library was opened.'
  }
}

interface CreateFailure {
  message: string
  registryPartialSuccess: boolean
}

function createFailure(error: unknown): CreateFailure {
  if (isLocalOnlyDenial(error)) {
    return {
      message: 'Library creation is available only from CrateIQ on the host machine.',
      registryPartialSuccess: false,
    }
  }
  const detail = apiErrorPayload(error)
  switch (apiErrorCode(error)) {
    case 'invalid_library_name':
      return { message: 'Enter a valid library name without path separators or command characters.', registryPartialSuccess: false }
    case 'library_name_collision':
      return { message: 'A file or folder with that name already exists in this location.', registryPartialSuccess: false }
    case 'unsafe_parent':
      return { message: 'That parent location is unavailable, read-only, or outside the allowed folders.', registryPartialSuccess: false }
    case 'initialization_failed':
      return {
        message: detail?.partial_directory_left === true
          ? 'Creation failed and a partial directory may remain. Inspect the location before trying another name.'
          : 'Creation failed. CrateIQ safely removed the operation-created directory.',
        registryPartialSuccess: false,
      }
    case 'registry_write_failed_after_create':
      return {
        message: 'The library was created on disk, but CrateIQ could not add it to the launcher. You can retry registration from Browse Libraries.',
        registryPartialSuccess: true,
      }
    default:
      return { message: 'CrateIQ could not create the library. Check the location and try again.', registryPartialSuccess: false }
  }
}

function validateLibraryName(name: string): string | null {
  if (!name.trim()) return 'Enter a library name.'
  if (name !== name.trim()) return 'Remove spaces from the start or end of the name.'
  if (/[\\/]/.test(name)) return 'Use a name without path separators.'
  if (name.length > MAX_LIBRARY_NAME_LENGTH) return `Use ${MAX_LIBRARY_NAME_LENGTH} characters or fewer.`
  return null
}

function formatLastOpened(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'Previously opened'
  return `Opened ${new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)}`
}

function FeedbackBanner({ feedback, busy = false }: { feedback: Feedback; busy?: boolean }) {
  return (
    <div
      className={`launcher-feedback launcher-feedback--${feedback.tone}`}
      role={feedback.tone === 'error' ? 'alert' : 'status'}
    >
      {busy && feedback.tone === 'info'
        ? <Loader2 className="spin" size={17} aria-hidden="true" />
        : feedback.tone === 'success'
          ? <Check size={17} aria-hidden="true" />
          : <CircleAlert size={17} aria-hidden="true" />}
      <span>{feedback.message}</span>
    </div>
  )
}

interface DirectoryBrowserProps {
  initialPath?: string
  disabled?: boolean
  selectDisabled?: boolean
  selectingPath?: string | null
  onSelect?: (entry: BrowseEntry) => void
  onLocationChange?: (location: BrowseResponse | null) => void
  onLocalOnly: () => void
  onLocalAccessConfirmed: () => void
}

function DirectoryBrowser({
  initialPath,
  disabled = false,
  selectDisabled = false,
  selectingPath = null,
  onSelect,
  onLocationChange,
  onLocalOnly,
  onLocalAccessConfirmed,
}: DirectoryBrowserProps) {
  const [location, setLocation] = useState<BrowseResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [canLoadMore, setCanLoadMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestController = useRef<AbortController | null>(null)
  const lastGoodLocation = useRef<BrowseResponse | null>(null)

  const loadLocation = useCallback(async (path?: string) => {
    requestController.current?.abort()
    const controller = new AbortController()
    requestController.current = controller
    setLoading(true)
    setError(null)
    onLocationChange?.(null)
    try {
      const next = await fetchBrowseDirectories({ path, limit: BROWSE_PAGE_SIZE }, controller.signal)
      lastGoodLocation.current = next
      setLocation(next)
      setCanLoadMore(next.truncated && next.entries.length === next.limit)
      onLocationChange?.(next)
      onLocalAccessConfirmed()
    } catch (loadError) {
      if (isAbortError(loadError)) return
      if (isLocalOnlyDenial(loadError)) onLocalOnly()
      setError(browseFailure(loadError))
      onLocationChange?.(lastGoodLocation.current)
    } finally {
      if (requestController.current === controller) {
        requestController.current = null
        setLoading(false)
      }
    }
  }, [onLocalAccessConfirmed, onLocalOnly, onLocationChange])

  const loadMore = useCallback(async () => {
    if (!location || loadingMore || !canLoadMore) return
    requestController.current?.abort()
    const controller = new AbortController()
    requestController.current = controller
    setLoadingMore(true)
    setError(null)
    try {
      const next = await fetchBrowseDirectories({
        path: location.current_path,
        offset: location.entries.length,
        limit: BROWSE_PAGE_SIZE,
      }, controller.signal)
      const merged = { ...next, offset: 0, entries: [...location.entries, ...next.entries] }
      lastGoodLocation.current = merged
      setLocation(merged)
      setCanLoadMore(next.truncated && next.entries.length === next.limit)
      onLocationChange?.(merged)
      onLocalAccessConfirmed()
    } catch (loadError) {
      if (isAbortError(loadError)) return
      if (isLocalOnlyDenial(loadError)) onLocalOnly()
      setError(browseFailure(loadError))
    } finally {
      if (requestController.current === controller) {
        requestController.current = null
        setLoadingMore(false)
      }
    }
  }, [canLoadMore, loadingMore, location, onLocalAccessConfirmed, onLocalOnly, onLocationChange])

  useEffect(() => {
    const timer = window.setTimeout(() => void loadLocation(initialPath), 0)
    return () => {
      window.clearTimeout(timer)
      requestController.current?.abort()
    }
  }, [initialPath, loadLocation])

  const interactionDisabled = disabled || loading

  return (
    <div className="launcher-browser" aria-busy={loading || loadingMore}>
      <div className="launcher-browser-toolbar">
        <button
          className="launcher-browser-back"
          type="button"
          onClick={() => void loadLocation(location?.parent_path ?? undefined)}
          disabled={!location?.parent_path || interactionDisabled}
          aria-label="Go to parent folder"
        >
          <ChevronLeft size={18} aria-hidden="true" />
        </button>
        <div className="launcher-browser-location">
          <span>Current location</span>
          <code title={location?.current_path}>{location?.current_path ?? 'Loading safe location…'}</code>
        </div>
      </div>

      {location && location.roots.length > 1 && (
        <div className="launcher-browser-roots" aria-label="Safe starting locations">
          {location.roots.map((root) => (
            <button
              type="button"
              key={root.path}
              onClick={() => void loadLocation(root.path)}
              disabled={interactionDisabled || root.path === location.current_path}
              aria-current={root.path === location.current_path ? 'location' : undefined}
              title={root.path}
            >
              {root.display_name}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="launcher-browser-error" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          <span>{error}</span>
          <button type="button" onClick={() => void loadLocation(location?.current_path ?? initialPath)}>
            Retry
          </button>
        </div>
      )}

      {loading && !location ? (
        <div className="launcher-browser-state" role="status">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading folders…</span>
        </div>
      ) : location && location.entries.length > 0 ? (
        <div className="launcher-directory-list" role="list" aria-label="Directories">
          {location.entries.map((entry) => {
            const isSelecting = selectingPath === entry.path
            const canOpen = entry.entry_type === 'directory'
            return (
              <div className="launcher-directory-row" role="listitem" key={entry.path}>
                <span className="launcher-directory-icon" aria-hidden="true">
                  {canOpen ? <Folder size={18} /> : <LockKeyhole size={17} />}
                </span>
                <div className="launcher-directory-copy">
                  <strong title={entry.display_name}>{entry.display_name}</strong>
                  <span>{classificationLabel(entry.classification)}</span>
                  <small>{entry.reason}</small>
                </div>
                <div className="launcher-directory-actions">
                  {canOpen && (
                    <button
                      className="btn btn--ghost"
                      type="button"
                      onClick={() => void loadLocation(entry.path)}
                      disabled={interactionDisabled}
                      aria-label={`Open ${entry.display_name}`}
                    >
                      Open <ChevronRight size={14} aria-hidden="true" />
                    </button>
                  )}
                  {entry.selectable && onSelect && (
                    <button
                      className="btn btn--primary"
                      type="button"
                      onClick={() => onSelect(entry)}
                      disabled={interactionDisabled || selectDisabled}
                      aria-label={`Register and open ${entry.display_name}`}
                    >
                      {isSelecting ? <><Loader2 className="spin" size={14} aria-hidden="true" /> Registering…</> : 'Select'}
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ) : location ? (
        <div className="launcher-browser-state">
          <FolderOpen size={20} aria-hidden="true" />
          <span>No directories are available here.</span>
        </div>
      ) : null}

      {loading && location && (
        <div className="launcher-browser-progress" role="status">
          <Loader2 className="spin" size={14} aria-hidden="true" /> Loading location…
        </div>
      )}

      {location && canLoadMore && (
        <button
          className="btn btn--ghost launcher-browser-more"
          type="button"
          onClick={() => void loadMore()}
          disabled={disabled || loadingMore}
        >
          {loadingMore ? <><Loader2 className="spin" size={15} aria-hidden="true" /> Loading…</> : 'Show more folders'}
        </button>
      )}
    </div>
  )
}

interface ActionDialogProps {
  initialPath?: string
  activatingLibraryId: string | null
  activationFeedback: Feedback | null
  launcherUnavailable: boolean
  onActivate: (library: ActivationTarget) => Promise<boolean>
  onLocalOnly: () => void
  onLocalAccessConfirmed: () => void
}

function BrowseDialogContent({
  initialPath,
  activatingLibraryId,
  activationFeedback,
  launcherUnavailable,
  onActivate,
  onLocalOnly,
  onLocalAccessConfirmed,
}: ActionDialogProps) {
  const [registeringPath, setRegisteringPath] = useState<string | null>(null)
  const [registered, setRegistered] = useState<RegisteredLibraryResponse | null>(null)
  const [activationFailed, setActivationFailed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestController = useRef<AbortController | null>(null)

  useEffect(() => () => requestController.current?.abort(), [])

  const activateResult = async (result: RegisteredLibraryResponse) => {
    setActivationFailed(false)
    const succeeded = await onActivate(result)
    if (!succeeded) setActivationFailed(true)
  }

  const selectDirectory = async (entry: BrowseEntry) => {
    if (!entry.selectable || registeringPath || activatingLibraryId) return
    const controller = new AbortController()
    requestController.current = controller
    setRegisteringPath(entry.path)
    setError(null)
    setRegistered(null)
    try {
      const result = await registerExistingLibrary({ path: entry.path }, controller.signal)
      setRegistered(result)
      await activateResult(result)
    } catch (registerError) {
      if (isAbortError(registerError)) return
      if (isLocalOnlyDenial(registerError)) onLocalOnly()
      setError(registerFailure(registerError))
    } finally {
      if (requestController.current === controller) requestController.current = null
      setRegisteringPath(null)
    }
  }

  return (
    <>
      {registered && (
        <div className="launcher-registration-result">
          <Check size={17} aria-hidden="true" />
          <div><strong>{registered.display_name} is registered</strong><span>Opening uses its launcher ID; the saved path is never sent to activation.</span></div>
        </div>
      )}
      {registered && activationFeedback && (
        <FeedbackBanner
          feedback={activationFeedback}
          busy={activatingLibraryId === registered.library_id}
        />
      )}
      {registered && activationFailed && (
        <button
          className="btn btn--primary launcher-retry-activation"
          type="button"
          onClick={() => void activateResult(registered)}
          disabled={Boolean(activatingLibraryId) || launcherUnavailable}
        >
          Retry opening {registered.display_name}
        </button>
      )}
      {error && <div className="launcher-dialog-error" role="alert"><CircleAlert size={16} aria-hidden="true" /><span>{error}</span></div>}
      <DirectoryBrowser
        initialPath={initialPath}
        disabled={Boolean(registeringPath || activatingLibraryId)}
        selectDisabled={launcherUnavailable}
        selectingPath={registeringPath}
        onSelect={(entry) => void selectDirectory(entry)}
        onLocalOnly={onLocalOnly}
        onLocalAccessConfirmed={onLocalAccessConfirmed}
      />
      {launcherUnavailable && (
        <div className="launcher-dialog-note">
          <CircleAlert size={16} aria-hidden="true" />
          <span>Folders can be inspected, but opening a library is unavailable until the local supervisor reconnects.</span>
        </div>
      )}
    </>
  )
}

interface CreateDialogProps extends ActionDialogProps {
  onSwitchToBrowse: (startPath?: string) => void
}

function CreateDialogContent({
  initialPath,
  activatingLibraryId,
  activationFeedback,
  launcherUnavailable,
  onActivate,
  onLocalOnly,
  onLocalAccessConfirmed,
  onSwitchToBrowse,
}: CreateDialogProps) {
  const [location, setLocation] = useState<BrowseResponse | null>(null)
  const [name, setName] = useState('')
  const [nameTouched, setNameTouched] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [created, setCreated] = useState<RegisteredLibraryResponse | null>(null)
  const [activationFailed, setActivationFailed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [registryPartialSuccess, setRegistryPartialSuccess] = useState(false)
  const requestController = useRef<AbortController | null>(null)
  const nameError = nameTouched ? validateLibraryName(name) : null

  useEffect(() => () => requestController.current?.abort(), [])

  const activateResult = async (result: RegisteredLibraryResponse) => {
    setActivationFailed(false)
    const succeeded = await onActivate(result)
    if (!succeeded) setActivationFailed(true)
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setNameTouched(true)
    const validationError = validateLibraryName(name)
    if (validationError || !location || submitting || created || registryPartialSuccess) return

    const controller = new AbortController()
    requestController.current = controller
    setSubmitting(true)
    setError(null)
    try {
      const result = await createLibrary({
        parent_directory: location.current_path,
        name,
      }, controller.signal)
      setCreated(result)
      await activateResult(result)
    } catch (createError) {
      if (isAbortError(createError)) return
      if (isLocalOnlyDenial(createError)) onLocalOnly()
      const failure = createFailure(createError)
      setError(failure.message)
      setRegistryPartialSuccess(failure.registryPartialSuccess)
    } finally {
      if (requestController.current === controller) requestController.current = null
      setSubmitting(false)
    }
  }

  return (
    <>
      {created && (
        <div className="launcher-registration-result">
          <Check size={17} aria-hidden="true" />
          <div><strong>{created.display_name} was created and registered</strong><span>CrateIQ is opening it through the existing launcher activation flow.</span></div>
        </div>
      )}
      {created && activationFeedback && (
        <FeedbackBanner
          feedback={activationFeedback}
          busy={activatingLibraryId === created.library_id}
        />
      )}
      {created && activationFailed && (
        <button
          className="btn btn--primary launcher-retry-activation"
          type="button"
          onClick={() => void activateResult(created)}
          disabled={Boolean(activatingLibraryId) || launcherUnavailable}
        >
          Retry opening {created.display_name}
        </button>
      )}
      {error && (
        <div className="launcher-dialog-error" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}
      {registryPartialSuccess && (
        <button
          className="btn btn--primary launcher-browse-recovery"
          type="button"
          onClick={() => onSwitchToBrowse(location?.current_path)}
        >
          <FolderOpen size={16} aria-hidden="true" /> Browse Libraries
        </button>
      )}

      <DirectoryBrowser
        initialPath={initialPath}
        disabled={submitting || Boolean(created) || registryPartialSuccess || Boolean(activatingLibraryId)}
        onLocationChange={setLocation}
        onLocalOnly={onLocalOnly}
        onLocalAccessConfirmed={onLocalAccessConfirmed}
      />

      <form className="launcher-create-form" onSubmit={(event) => void submit(event)} noValidate>
        <div className="launcher-create-location">
          <span>Parent directory</span>
          <code title={location?.current_path}>{location?.current_path ?? 'Choose a safe location above'}</code>
        </div>
        <label htmlFor="launcher-library-name">Library name</label>
        <input
          id="launcher-library-name"
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
          onBlur={() => setNameTouched(true)}
          maxLength={MAX_LIBRARY_NAME_LENGTH}
          required
          disabled={submitting || Boolean(created) || registryPartialSuccess}
          aria-invalid={Boolean(nameError)}
          aria-describedby={nameError ? 'launcher-library-name-error launcher-library-name-hint' : 'launcher-library-name-hint'}
          placeholder="My CrateIQ Library"
          autoComplete="off"
        />
        <div className="launcher-create-hint" id="launcher-library-name-hint">
          One folder name, up to {MAX_LIBRARY_NAME_LENGTH} characters. The backend performs final validation.
        </div>
        {nameError && <div className="launcher-field-error" id="launcher-library-name-error" role="alert">{nameError}</div>}
        <button
          className="btn btn--primary launcher-create-submit"
          type="submit"
          disabled={!location || Boolean(validateLibraryName(name)) || submitting || Boolean(created) || registryPartialSuccess || Boolean(activatingLibraryId) || launcherUnavailable}
        >
          {submitting ? <><Loader2 className="spin" size={16} aria-hidden="true" /> Creating…</> : <><Plus size={16} aria-hidden="true" /> Create and open</>}
        </button>
      </form>
      {launcherUnavailable && (
        <div className="launcher-dialog-note">
          <CircleAlert size={16} aria-hidden="true" />
          <span>Creation is paused because the local supervisor cannot open the new library right now.</span>
        </div>
      )}
    </>
  )
}

interface EntryPointDialogProps extends ActionDialogProps {
  kind: DialogKind
  localOnlyUnavailable: boolean
  onClose: () => void
  onSwitchToBrowse: (startPath?: string) => void
}

function EntryPointDialog({
  kind,
  initialPath,
  localOnlyUnavailable,
  activatingLibraryId,
  activationFeedback,
  launcherUnavailable,
  onActivate,
  onClose,
  onLocalOnly,
  onLocalAccessConfirmed,
  onSwitchToBrowse,
}: EntryPointDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const isBrowse = kind === 'browse'

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (!dialog.open) dialog.showModal()
  }, [])

  return (
    <dialog
      ref={dialogRef}
      className="launcher-dialog launcher-dialog--browser"
      aria-labelledby={`launcher-${kind}-title`}
      aria-describedby={`launcher-${kind}-description`}
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
      <h2 id={`launcher-${kind}-title`}>{isBrowse ? 'Browse Libraries' : 'Create New Library'}</h2>
      <p id={`launcher-${kind}-description`}>
        {isBrowse
          ? 'Choose a backend-validated library, register it, and open it through the existing launcher flow.'
          : 'Choose a safe parent folder, name the library, and let CrateIQ initialize and open it.'}
      </p>

      <div className="launcher-dialog-body">
        {localOnlyUnavailable ? (
          <div className="launcher-local-only" role="status">
            <LockKeyhole size={21} aria-hidden="true" />
            <div>
              <strong>Available on the host machine only</strong>
              <p>Browse and Create cannot expose the host filesystem over LAN. Registered libraries can still be opened from the launcher.</p>
            </div>
            <button className="btn btn--primary" type="button" onClick={onClose}>Close</button>
          </div>
        ) : isBrowse ? (
          <BrowseDialogContent
            initialPath={initialPath}
            activatingLibraryId={activatingLibraryId}
            activationFeedback={activationFeedback}
            launcherUnavailable={launcherUnavailable}
            onActivate={onActivate}
            onLocalOnly={onLocalOnly}
            onLocalAccessConfirmed={onLocalAccessConfirmed}
          />
        ) : (
          <CreateDialogContent
            initialPath={initialPath}
            activatingLibraryId={activatingLibraryId}
            activationFeedback={activationFeedback}
            launcherUnavailable={launcherUnavailable}
            onActivate={onActivate}
            onLocalOnly={onLocalOnly}
            onLocalAccessConfirmed={onLocalAccessConfirmed}
            onSwitchToBrowse={onSwitchToBrowse}
          />
        )}
      </div>
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
  const [localAdminStatus, setLocalAdminStatus] = useState<LocalAdminStatus>('checking')
  const [dialog, setDialog] = useState<DialogState | null>(null)
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

  useEffect(() => {
    let controller: AbortController | null = null
    const timer = window.setTimeout(() => {
      controller = new AbortController()
      void fetchBrowseDirectories({}, controller.signal)
        .then(() => setLocalAdminStatus('available'))
        .catch((error: unknown) => {
          if (isAbortError(error)) return
          setLocalAdminStatus(isLocalOnlyDenial(error) ? 'unavailable' : 'error')
        })
    }, 0)
    return () => {
      window.clearTimeout(timer)
      controller?.abort()
    }
  }, [])

  useEffect(() => () => activationController.current?.abort(), [])

  const verifyCurrentLibrary = useCallback(async (libraryId: string, signal: AbortSignal): Promise<boolean> => {
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
  }, [])

  const activateLibrary = useCallback(async (library: ActivationTarget): Promise<boolean> => {
    if (activationController.current) return false

    const controller = new AbortController()
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
            return false
          }
          await fetchLibraryRegistry(controller.signal).then(setRegistry).catch(() => undefined)
          navigate('/', { replace: true })
          return true
        }

        if (['blocked', 'failed', 'fail_closed'].includes(status.activation_status)) {
          setFeedback({ tone: 'error', message: activationFailure(status) })
          return false
        }
      }

      setFeedback({
        tone: 'warning',
        message: 'CrateIQ could not verify the library switch in time. Refresh the launcher before trying again.',
      })
      return false
    } catch (error) {
      if (isAbortError(error)) return false
      setFeedback({ tone: 'error', message: activationStartFailure(error) })
      return false
    } finally {
      if (!controller.signal.aborted) setActivatingLibraryId(null)
      if (activationController.current === controller) activationController.current = null
    }
  }, [navigate, verifyCurrentLibrary])

  const selectLibrary = (library: RecentLibrary) => {
    if (!library.availability) return
    void activateLibrary(library)
  }

  const confirmLocalAccess = useCallback(() => setLocalAdminStatus('available'), [])
  const markLocalOnly = useCallback(() => setLocalAdminStatus('unavailable'), [])

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
        </div>
      </section>

      <section className="launcher-content" aria-labelledby="launcher-title">
        <div className="launcher-content-inner">
          <header className="launcher-heading">
            <span className="launcher-heading-icon" aria-hidden="true"><LibraryBig size={19} /></span>
            <div>
              <h2 id="launcher-title">Choose your library</h2>
              <p>Open a recent library or create a new one.</p>
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

              {feedback && <FeedbackBanner feedback={feedback} busy={Boolean(activatingLibraryId)} />}

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
                  <div><strong>No recent libraries</strong><p>You haven’t opened any libraries yet.</p></div>
                </div>
              )}

              <div className="launcher-entry-actions">
                <button
                  className="btn btn--ghost"
                  type="button"
                  onClick={() => setDialog({ kind: 'browse' })}
                  disabled={localAdminStatus === 'checking' || localAdminStatus === 'unavailable'}
                  aria-describedby={localAdminStatus === 'unavailable' ? 'launcher-local-admin-status' : undefined}
                >
                  <FolderOpen size={17} /> Browse Libraries
                </button>
                <button
                  className="btn btn--primary"
                  type="button"
                  onClick={() => setDialog({ kind: 'create' })}
                  disabled={localAdminStatus === 'checking' || localAdminStatus === 'unavailable'}
                  aria-describedby={localAdminStatus === 'unavailable' ? 'launcher-local-admin-status' : undefined}
                >
                  <Plus size={17} /> Create New Library
                </button>
              </div>

              {localAdminStatus === 'checking' && (
                <div className="launcher-local-admin-status" role="status">
                  <Loader2 className="spin" size={14} aria-hidden="true" /> Checking local filesystem access…
                </div>
              )}
              {localAdminStatus === 'unavailable' && (
                <div className="launcher-local-admin-status" id="launcher-local-admin-status" role="status">
                  <LockKeyhole size={14} aria-hidden="true" /> Browse and Create are local-only. Registered libraries remain available to open.
                </div>
              )}
              {localAdminStatus === 'error' && (
                <div className="launcher-local-admin-status launcher-local-admin-status--warning" role="status">
                  <CircleAlert size={14} aria-hidden="true" /> Local filesystem access could not be confirmed. Open an action to retry.
                </div>
              )}
            </>
          )}
        </div>
      </section>

      {dialog && (
        <EntryPointDialog
          key={`${dialog.kind}:${dialog.startPath ?? ''}`}
          kind={dialog.kind}
          initialPath={dialog.startPath}
          localOnlyUnavailable={localAdminStatus === 'unavailable'}
          activatingLibraryId={activatingLibraryId}
          activationFeedback={feedback}
          launcherUnavailable={launcherUnavailable}
          onActivate={activateLibrary}
          onClose={() => setDialog(null)}
          onLocalOnly={markLocalOnly}
          onLocalAccessConfirmed={confirmLocalAccess}
          onSwitchToBrowse={(startPath) => setDialog({ kind: 'browse', startPath })}
        />
      )}
    </main>
  )
}
