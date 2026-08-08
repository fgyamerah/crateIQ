import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArchiveX,
  CheckCircle2,
  CircleAlert,
  Copy,
  FolderInput,
  HardDrive,
  Library as LibraryIcon,
  ShieldCheck,
} from 'lucide-react'
import { ApiError } from '../../api/client'
import { updateLibraryRoot } from '../../api/settings'
import { classifyWorkspaceRoot, configureWorkspace, createWorkspaceRoot } from '../../api/workspace'
import type { WorkspaceRootClassification, WorkspaceStatus } from '../../api/workspace'
import type { SettingsResponse } from '../../types/settings'
import Badge from '../ui/Badge'
import StatusStrip from '../ui/StatusStrip'

function errorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.displayMessage : fallback
}

interface Props {
  settings: SettingsResponse
  workspace: WorkspaceStatus | null
  onReload: () => Promise<void>
}

function ZonePreview() {
  return (
    <div className="workspace-zone-preview">
      <div><FolderInput size={15} /><strong>Inbox</strong><span>Music being prepared</span></div>
      <div><LibraryIcon size={15} /><strong>Library</strong><span>Finished music</span></div>
      <div><ArchiveX size={15} /><strong>Quarantine</strong><span>Isolated problem files</span></div>
    </div>
  )
}

export default function WorkspacePanel({ settings, workspace, onReload }: Props) {
  const [formOpen, setFormOpen] = useState(false)
  const [rootInput, setRootInput] = useState('')
  const [classification, setClassification] = useState<WorkspaceRootClassification | null>(null)
  const [checking, setChecking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [runtimeOpen, setRuntimeOpen] = useState(false)

  const restartPending = settings.library.restart_required
  const isDemo = settings.library.mode === 'demo'
  const state: 'pending' | 'managed_workspace' | 'legacy_direct_library' | 'not_configured' =
    restartPending ? 'pending' : (workspace?.state ?? 'not_configured')

  const resetForm = () => {
    setFormOpen(false)
    setRootInput('')
    setClassification(null)
    setError(null)
  }

  const runClassify = async () => {
    const value = rootInput.trim()
    if (!value) return
    setChecking(true)
    setError(null)
    setClassification(null)
    try {
      setClassification(await classifyWorkspaceRoot(value))
    } catch (err) {
      setError(errorMessage(err, 'Could not check that folder.'))
    } finally {
      setChecking(false)
    }
  }

  const saveRoot = async () => {
    const value = rootInput.trim()
    if (!value || !classification) return
    setBusy(true)
    setError(null)
    try {
      if (classification.can_create) {
        await createWorkspaceRoot(value)
      }
      await updateLibraryRoot(value)
      resetForm()
      await onReload()
    } catch (err) {
      setError(errorMessage(err, 'Could not save this workspace.'))
    } finally {
      setBusy(false)
    }
  }

  const initializeEmptyRoot = async () => {
    setBusy(true)
    setError(null)
    try {
      await configureWorkspace()
      await onReload()
    } catch (err) {
      setError(errorMessage(err, 'Could not create the managed workspace.'))
    } finally {
      setBusy(false)
    }
  }

  const copyRestartCommand = async () => {
    try {
      await navigator.clipboard.writeText(settings.library.restart_command)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setError('Could not copy automatically. Copy the command shown below instead.')
    }
  }

  const rootPicker = (
    <div className="workspace-root-picker">
      <label>Workspace folder
        <input
          className="form-input"
          value={rootInput}
          onChange={(event) => { setRootInput(event.target.value); setClassification(null); setError(null) }}
          placeholder="/home/user/Music/crateIQ"
          disabled={checking || busy}
        />
      </label>
      <div className="settings-action-row">
        <button className="btn btn--ghost" disabled={checking || busy || !rootInput.trim()} onClick={() => void runClassify()}>
          {checking ? 'Checking…' : 'Validate'}
        </button>
        {formOpen && <button className="btn btn--ghost" disabled={busy} onClick={resetForm}>Cancel</button>}
      </div>
      {classification && (
        classification.state === 'legacy_direct_library' ? (
          <>
            <StatusStrip tone="warn" icon={<CircleAlert size={15} />}>
              Existing music folder detected. This folder already contains music or CrateIQ direct-library data.
              CrateIQ will not reorganize or move anything inside it.
            </StatusStrip>
            <p className="muted settings-note">To use the Inbox → Library workflow, choose a new dedicated folder instead.</p>
            <div className="settings-action-row">
              <button className="btn btn--primary btn--sm" disabled={busy} onClick={() => { setClassification(null); setRootInput('') }}>Choose a different folder</button>
              <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void saveRoot()}>
                {busy ? 'Saving…' : 'Advanced: save as Legacy Direct Library'}
              </button>
            </div>
          </>
        ) : classification.state === 'managed_workspace' ? (
          <>
            <StatusStrip tone="good" icon={<CheckCircle2 size={15} />}>This folder is already a managed CrateIQ workspace.</StatusStrip>
            <div className="settings-action-row">
              <button className="btn btn--primary" disabled={busy} onClick={() => void saveRoot()}>{busy ? 'Saving…' : 'Use This Workspace'}</button>
            </div>
          </>
        ) : classification.can_create || classification.exists ? (
          <>
            <StatusStrip tone="info" icon={<HardDrive size={15} />}>
              {classification.exists
                ? 'This empty folder is ready to become a managed workspace.'
                : 'This folder does not exist yet. CrateIQ will create only this one folder — no parent folders are created.'}
            </StatusStrip>
            <ZonePreview />
            <p className="muted settings-note">Original music you import from elsewhere will be copied here. The originals remain untouched.</p>
            <div className="settings-action-row">
              <button className="btn btn--primary" disabled={busy} onClick={() => void saveRoot()}>
                {busy ? 'Saving…' : 'Set Up Workspace'}
              </button>
            </div>
          </>
        ) : (
          <StatusStrip tone="danger" icon={<CircleAlert size={15} />}>{classification.message}</StatusStrip>
        )
      )}
    </div>
  )

  return (
    <section className="section" id="workspace">
      <div className="card settings-card workspace-panel">
        <div className="card-title-row">
          <div>
            <h2 className="card-title"><HardDrive size={16} /> Managed Workspace</h2>
            <p className="muted">Where CrateIQ keeps the music it manages, separate from the original files you import from.</p>
          </div>
          <Badge tone={state === 'managed_workspace' ? 'succeeded' : state === 'pending' ? 'pending' : state === 'legacy_direct_library' ? 'pending' : 'info'}>
            {state === 'managed_workspace' ? 'Ready' : state === 'pending' ? 'Restart required' : state === 'legacy_direct_library' ? 'Existing folder' : 'Not set up'}
          </Badge>
        </div>

        {error && <StatusStrip tone="danger" onDismiss={() => setError(null)}>{error}</StatusStrip>}

        {state === 'pending' ? (
          <>
            <StatusStrip tone="warn" icon={<CircleAlert size={15} />}>
              Workspace change pending restart. CrateIQ is still using the current workspace until restart.
            </StatusStrip>
            <div className="workspace-pending-columns">
              <div>
                <strong>Current workspace</strong>
                <code>{settings.library.library_root}</code>
              </div>
              <div>
                <strong>New workspace</strong>
                <code>{settings.library.pending_library_root}</code>
              </div>
            </div>
            <div className="settings-action-row">
              <button className="btn btn--primary btn--sm" onClick={() => void copyRestartCommand()}>
                <Copy size={13} /> {copied ? 'Copied' : 'Copy Restart Command'}
              </button>
            </div>
            <pre className="workspace-restart-command"><code>{settings.library.restart_command}</code></pre>
            <p className="muted settings-note">Changing workspace does not move music from the current workspace. Restart CrateIQ, then reload this page.</p>
            <details className="workspace-runtime-details" open={runtimeOpen} onToggle={(event) => setRuntimeOpen(event.currentTarget.open)}>
              <summary>Current runtime — until restart</summary>
              <dl className="def-list settings-def-list">
                <dt>Processed database</dt><dd><code>{settings.library.processed_db}</code></dd>
                <dt>Manual Crates database</dt><dd><code>{settings.library.manual_crates_db}</code></dd>
                <dt>Exports root</dt><dd><code>{settings.library.exports_root}</code></dd>
              </dl>
            </details>
          </>
        ) : state === 'managed_workspace' && workspace ? (
          <>
            <dl className="def-list settings-def-list">
              <dt>Root</dt><dd><code>{workspace.library_root}</code></dd>
              <dt>Inbox</dt><dd><code>{workspace.inbox_path}</code></dd>
              <dt>Library</dt><dd><code>{workspace.library_path}</code></dd>
              <dt>Quarantine</dt><dd><code>{workspace.quarantine_path}</code></dd>
            </dl>
            <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>
              Imported external music is copied into Inbox. Original sources remain untouched.
            </StatusStrip>
            <div className="settings-action-row">
              <Link className="btn btn--primary" to="/inbox">Open Inbox</Link>
              <button className="btn btn--ghost" onClick={() => setFormOpen((open) => !open)}>{formOpen ? 'Cancel' : 'Change Workspace'}</button>
            </div>
            {formOpen && <>
              <p className="muted settings-note">Changing workspace does not move music from the current workspace.</p>
              {rootPicker}
            </>}
          </>
        ) : state === 'legacy_direct_library' && workspace ? (
          <>
            <StatusStrip tone="warn" icon={<CircleAlert size={15} />}>
              Existing music folder detected. This folder already contains music or CrateIQ direct-library data.
              CrateIQ will not reorganize or move anything inside it.
            </StatusStrip>
            <dl className="def-list settings-def-list">
              <dt>Existing music folder</dt><dd><code>{workspace.library_root}</code></dd>
            </dl>
            <p className="muted settings-note">To use the Inbox → Library workflow, choose a new dedicated workspace folder.</p>
            <div className="settings-action-row">
              <button className="btn btn--primary" onClick={() => setFormOpen(true)}>Choose New Managed Workspace</button>
              <a className="btn btn--ghost btn--sm" href="#legacy-direct-library">Advanced: continue using Legacy Direct Library</a>
            </div>
            {formOpen && rootPicker}
          </>
        ) : isDemo ? (
          <>
            <p>This dedicated folder will contain:</p>
            <ZonePreview />
            <p className="muted settings-note">Original music you import from elsewhere will be copied here. The originals remain untouched.</p>
            {rootPicker}
          </>
        ) : (
          <>
            <StatusStrip tone="info" icon={<HardDrive size={15} />}>Ready to initialize managed workspace.</StatusStrip>
            <dl className="def-list settings-def-list">
              <dt>Root</dt><dd><code>{settings.library.library_root}</code></dd>
            </dl>
            <div className="settings-action-row">
              <button className="btn btn--primary" disabled={busy} onClick={() => void initializeEmptyRoot()}>
                {busy ? 'Creating…' : 'Create Managed Workspace'}
              </button>
              <button className="btn btn--ghost" onClick={() => setFormOpen((open) => !open)}>{formOpen ? 'Cancel' : 'Change Workspace'}</button>
            </div>
            {formOpen && rootPicker}
          </>
        )}
      </div>
    </section>
  )
}
