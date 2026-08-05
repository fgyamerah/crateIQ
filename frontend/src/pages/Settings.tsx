import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, CircleAlert, FolderCog, RefreshCw, Settings2, ShieldCheck, Wrench } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchSettings, fetchSettingsRuntime, updateSettings } from '../api/settings'
import type { CheckStatus, RuntimeReadiness, SettingsResponse } from '../types/settings'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'

function badgeTone(status: CheckStatus) {
  return status === 'pass' ? 'succeeded' : status === 'warn' ? 'pending' : 'failed'
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.displayMessage : fallback
}

export default function Settings() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null)
  const [runtime, setRuntime] = useState<RuntimeReadiness | null>(null)
  const [pathMode, setPathMode] = useState<SettingsResponse['preferences']['default_export_path_mode']>('filename')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [nextSettings, nextRuntime] = await Promise.all([fetchSettings(), fetchSettingsRuntime()])
      setSettings(nextSettings); setRuntime(nextRuntime); setPathMode(nextSettings.preferences.default_export_path_mode)
    } catch (err) { setError(errorMessage(err, 'Could not load local settings.')) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { void load() }, [load])

  const savePreference = async () => {
    setBusy(true); setError(null); setSaved(false)
    try {
      const next = await updateSettings(pathMode)
      setSettings(next); setPathMode(next.preferences.default_export_path_mode); setSaved(true)
    } catch (err) { setError(errorMessage(err, 'Could not save the export path preference.')) }
    finally { setBusy(false) }
  }

  const recheck = async () => {
    setBusy(true); setError(null)
    try {
      const [nextSettings, nextRuntime] = await Promise.all([fetchSettings(), fetchSettingsRuntime()])
      setSettings(nextSettings); setRuntime(nextRuntime); setPathMode(nextSettings.preferences.default_export_path_mode)
    } catch (err) { setError(errorMessage(err, 'Could not recheck local runtime readiness.')) }
    finally { setBusy(false) }
  }

  const readiness = runtime?.status ?? settings?.library.readiness_status
  const readyToolCount = settings?.tools.filter((tool) => tool.status === 'pass').length ?? 0

  return <div className="page settings-page">
    <PageHeader title="Settings" subtitle="Inspect local runtime readiness and save only safe, library-scoped preferences." actions={<button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void recheck()}><RefreshCw size={13} className={busy ? 'spin' : ''} /> Recheck readiness</button>} />
    <StatusStrip tone="info" icon={<Settings2 size={15} />} footnote="Local-first diagnostics · no folders are scanned and no music or DJ application data is changed.">Library roots and tool overrides are process-start configuration. This page shows their current state and restart guidance without changing them live.</StatusStrip>
    {error && <StatusStrip tone="danger" role="alert" onDismiss={() => setError(null)}>{error}</StatusStrip>}
    {saved && <StatusStrip tone="good" onDismiss={() => setSaved(false)}>Saved the default export path mode in this library’s local app settings.</StatusStrip>}
    {loading ? <EmptyState title="Loading settings" message="Reading local runtime diagnostics…" /> : !settings ? <EmptyState title="Settings unavailable" message="Check that the local backend is running, then recheck readiness." /> : <>
      <div className="settings-kpis"><KpiCard tone="cyan" icon={<FolderCog size={18} />} label="Library mode" value={settings.library.mode === 'demo' ? 'Demo' : 'Configured'} sub={settings.library.readiness_status.replace('_', ' ')} /><KpiCard tone="emerald" icon={<Wrench size={18} />} label="Optional tools" value={`${readyToolCount}/${settings.tools.length}`} sub="Detected for local workflows" /><KpiCard tone="violet" icon={<ShieldCheck size={18} />} label="Safety policies" value="Locked" sub="MIK and DJ app protections" /></div>

      <section className="section"><div className="card settings-card"><div className="card-title-row"><div><h2 className="card-title">Library</h2><p className="muted">Current selected-library paths are display-safe and read-only here.</p></div><Badge tone={readiness === 'ready' ? 'succeeded' : 'pending'}>{readiness?.replace('_', ' ')}</Badge></div><dl className="def-list settings-def-list"><dt>Active library root</dt><dd><code>{settings.library.library_root}</code></dd><dt>Processed database</dt><dd><code>{settings.library.processed_db}</code></dd><dt>Manual Crates database</dt><dd><code>{settings.library.manual_crates_db}</code></dd><dt>Exports root</dt><dd><code>{settings.library.exports_root}</code></dd></dl><StatusStrip tone="warn" icon={<CircleAlert size={15} />}>Changing <code>CRATEIQ_LIBRARY_ROOT</code> or an executable override is not available in-app. Update your private environment, then restart:<br /><code>scripts/crateiq-local-services.sh stop</code><br /><code>scripts/crateiq-local-services.sh start</code></StatusStrip></div></section>

      <section className="section"><div className="card settings-card"><div className="card-title-row"><div><h2 className="card-title">Preferences</h2><p className="muted">This is the only editable setting in this first release.</p></div><Badge tone="info">Local preference</Badge></div><div className="settings-preference"><label>Default export path mode<select className="form-input" value={pathMode} onChange={(event) => setPathMode(event.target.value as typeof pathMode)} disabled={busy}><option value="filename">Filename (safe default)</option><option value="relative">Relative to library root</option><option value="absolute">Absolute path</option></select></label><button className="btn btn--primary" disabled={busy || pathMode === settings.preferences.default_export_path_mode} onClick={() => void savePreference()}>Save preference</button></div><p className="muted settings-note">Saved locally under the selected library. Existing export forms continue to require explicit preview and write actions.</p></div></section>

      <section className="section"><div className="card settings-card"><div className="card-title-row"><div><h2 className="card-title">Tools</h2><p className="muted">Availability is detected without invoking analysis or scanning tracks.</p></div><Badge tone="info">Runtime diagnostics</Badge></div><div className="settings-tool-list">{settings.tools.map((tool) => <div className="settings-tool" key={tool.name}><div><strong>{tool.name}</strong><span>{tool.message}</span>{tool.resolved && <small>Source: {tool.source} · {tool.resolved}</small>}{!tool.resolved && <small>Configure with {tool.source} in a private environment if this workflow is needed.</small>}</div><Badge tone={badgeTone(tool.status)}>{tool.status}</Badge></div>)}</div></div></section>

      <section className="section"><div className="card settings-card"><div className="card-title-row"><div><h2 className="card-title">Safety policies</h2><p className="muted">These are deliberate product rules, not editable toggles.</p></div><Badge tone="succeeded">Locked</Badge></div><ul className="settings-safety-list"><li><CheckCircle2 size={15} /> Mixed In Key remains authoritative for BPM, key, and cues.</li><li><CheckCircle2 size={15} /> Analysis fills missing data only; files and tags are never automatically modified.</li><li><CheckCircle2 size={15} /> Serato and Rekordbox live databases are never written by crateIQ.</li><li><CheckCircle2 size={15} /> Export and apply workflows remain preview-first.</li></ul></div></section>

      <section className="section"><div className="card settings-card"><div className="card-title-row"><div><h2 className="card-title">Runtime diagnostics</h2><p className="muted">Read-only checks for the selected root, pipeline database, and optional tools.</p></div><a className="btn btn--ghost btn--xs" href="http://127.0.0.1:8020/api/runtime/readiness" target="_blank" rel="noreferrer">Open JSON</a></div><StatusStrip tone={readiness === 'ready' ? 'good' : readiness === 'not_ready' ? 'danger' : 'warn'}>{readiness === 'ready' ? 'Local runtime is ready.' : readiness === 'not_ready' ? 'A required local runtime check needs attention.' : 'Local runtime is usable with optional workflow warnings.'}</StatusStrip><div className="settings-runtime-list">{runtime?.checks.map((check) => <div className="settings-runtime-check" key={check.name}><Badge tone={badgeTone(check.status)}>{check.status}</Badge><div><strong>{check.name.replace(/_/g, ' ')}</strong><span>{check.message}</span></div></div>)}</div><p className="muted settings-note">Copyable readiness command: <code>curl -s http://127.0.0.1:8020/api/runtime/readiness | python3 -m json.tool</code></p></div></section>
    </>}
  </div>
}
