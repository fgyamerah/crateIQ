import { useCallback, useEffect, useState } from 'react'
import {
  CheckCircle2,
  CircleAlert,
  FolderCog,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Wrench,
} from 'lucide-react'
import { ApiError } from '../api/client'
import {
  fetchSettings,
  fetchSettingsRuntime,
  importLibrary,
  initializeLibrary,
  scanLibraryPreview,
  updateLibraryRoot,
  updateSettings,
  validateLibraryRoot,
} from '../api/settings'
import type {
  AnalysisPreferences,
  CheckStatus,
  LibraryRootValidation,
  LibrarySetupResult,
  RuntimeReadiness,
  SettingsResponse,
  WorkflowCapability,
} from '../types/settings'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'

function badgeTone(status: CheckStatus) {
  return status === 'pass' ? 'succeeded' : status === 'warn' ? 'pending' : 'failed'
}

function capabilityTone(capability: WorkflowCapability) {
  return capability.status === 'available'
    ? 'succeeded'
    : capability.status === 'missing'
      ? 'failed'
      : 'pending'
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.displayMessage : fallback
}

function capabilityRequirement(capability: WorkflowCapability) {
  if (capability.required_source) return capability.required_source
  if (capability.required_tools?.length) return capability.required_tools.join(' + ')
  return capability.required_tool ?? 'No optional tool'
}

function CapabilityCard({ label, capability }: { label: string; capability: WorkflowCapability }) {
  const actionLabel = capability.action_state === 'setup_required'
    ? 'Missing tool'
    : capability.action_state === 'coming_soon'
      ? 'Coming soon'
      : 'Run coming soon'

  return (
    <div className="settings-capability">
      <div>
        <div className="settings-capability-title">
          <strong>{label}</strong>
          <Badge tone={capabilityTone(capability)}>{capability.status.replace('_', ' ')}</Badge>
        </div>
        <span>{capability.purpose}</span>
        <small>Requires: {capabilityRequirement(capability)}</small>
        <small>{capability.message}</small>
      </div>
      <button className="btn btn--ghost btn--xs" type="button" disabled>
        {actionLabel}
      </button>
    </div>
  )
}

export default function Settings() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null)
  const [runtime, setRuntime] = useState<RuntimeReadiness | null>(null)
  const [pathMode, setPathMode] = useState<SettingsResponse['preferences']['default_export_path_mode']>('filename')
  const [analysis, setAnalysis] = useState<AnalysisPreferences>({
    analyze_bpm: false,
    analyze_key: false,
    use_mik_when_present: true,
    preserve_existing_bpm_key_cues: true,
    missing_data_only: true,
    use_external_tools: true,
  })
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [analysisBusy, setAnalysisBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [analysisSaved, setAnalysisSaved] = useState(false)
  const [libraryRootInput, setLibraryRootInput] = useState('')
  const [libraryValidation, setLibraryValidation] = useState<LibraryRootValidation | null>(null)
  const [libraryBusy, setLibraryBusy] = useState(false)
  const [librarySaved, setLibrarySaved] = useState(false)
  const [setupBusy, setSetupBusy] = useState(false)
  const [setupResult, setSetupResult] = useState<LibrarySetupResult | null>(null)

  const applySettings = (next: SettingsResponse) => {
    setSettings(next)
    setPathMode(next.preferences.default_export_path_mode)
    setAnalysis(next.preferences.analysis)
  }

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [nextSettings, nextRuntime] = await Promise.all([fetchSettings(), fetchSettingsRuntime()])
      applySettings(nextSettings)
      setRuntime(nextRuntime)
    } catch (err) {
      setError(errorMessage(err, 'Could not load local settings.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const savePreference = async () => {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      applySettings(await updateSettings({ default_export_path_mode: pathMode }))
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err, 'Could not save the export path preference.'))
    } finally {
      setBusy(false)
    }
  }

  const saveAnalysis = async () => {
    setAnalysisBusy(true)
    setError(null)
    setAnalysisSaved(false)
    try {
      applySettings(await updateSettings({
        analysis: {
          analyze_bpm: analysis.analyze_bpm,
          analyze_key: analysis.analyze_key,
          use_external_tools: analysis.use_external_tools,
        },
      }))
      setAnalysisSaved(true)
    } catch (err) {
      setError(errorMessage(err, 'Could not save optional analysis preferences.'))
    } finally {
      setAnalysisBusy(false)
    }
  }

  const recheck = async () => {
    setBusy(true)
    setError(null)
    try {
      const [nextSettings, nextRuntime] = await Promise.all([fetchSettings(), fetchSettingsRuntime()])
      applySettings(nextSettings)
      setRuntime(nextRuntime)
    } catch (err) {
      setError(errorMessage(err, 'Could not recheck local runtime readiness.'))
    } finally {
      setBusy(false)
    }
  }

  const validateRoot = async () => {
    setLibraryBusy(true)
    setError(null)
    setLibrarySaved(false)
    try {
      setLibraryValidation(await validateLibraryRoot(libraryRootInput))
    } catch (err) {
      setLibraryValidation(null)
      setError(errorMessage(err, 'Could not validate the library root.'))
    } finally {
      setLibraryBusy(false)
    }
  }

  const saveLibraryRoot = async () => {
    setLibraryBusy(true)
    setError(null)
    setLibrarySaved(false)
    try {
      applySettings(await updateLibraryRoot(libraryRootInput))
      setLibraryValidation(null)
      setLibrarySaved(true)
    } catch (err) {
      setError(errorMessage(err, 'Could not save the pending library root.'))
    } finally {
      setLibraryBusy(false)
    }
  }

  const runSetup = async (action: 'initialize' | 'preview' | 'import') => {
    setSetupBusy(true)
    setError(null)
    try {
      const result = action === 'initialize'
        ? await initializeLibrary()
        : action === 'preview'
          ? await scanLibraryPreview()
          : await importLibrary()
      setSetupResult(result)
      applySettings(await fetchSettings())
    } catch (err) {
      const label = action === 'preview' ? 'preview the scan' : `${action} the library`
      setError(errorMessage(err, `Could not ${label}.`))
    } finally {
      setSetupBusy(false)
    }
  }

  const readiness = runtime?.status ?? settings?.library.readiness_status
  const readyToolCount = settings?.tools.filter((tool) => tool.status === 'pass').length ?? 0
  const setupInitialized = Boolean(settings && (
    settings.library.library_initialized
    || settings.library.pending_library_initialized
    || (settings.library.mode === 'configured'
      && !settings.library.restart_required
      && settings.library.readiness_status === 'ready'
      && settings.library.processed_db)
  ))
  const analysisChanged = settings != null && (
    analysis.analyze_bpm !== settings.preferences.analysis.analyze_bpm
    || analysis.analyze_key !== settings.preferences.analysis.analyze_key
    || analysis.use_external_tools !== settings.preferences.analysis.use_external_tools
  )
  const optionalAnalysisWarnings = settings ? [
    analysis.analyze_bpm && !settings.capabilities.analysis.bpm_analysis.available
      ? 'BPM analysis is enabled as a preference, but aubio is not currently available.'
      : null,
    analysis.analyze_key && !settings.capabilities.analysis.key_analysis.available
      ? 'Key/Camelot analysis is enabled as a preference, but keyfinder-cli is not currently available.'
      : null,
  ].filter((message): message is string => Boolean(message)) : []

  return (
    <div className="page settings-page">
      <PageHeader
        title="Settings"
        subtitle="Set up the local library, then choose optional analysis only when you need it."
        actions={(
          <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void recheck()}>
            <RefreshCw size={13} className={busy ? 'spin' : ''} /> Recheck readiness
          </button>
        )}
      />
      <StatusStrip tone="info" icon={<Settings2 size={15} />} footnote="Local-first diagnostics · no folders are scanned and no music or DJ application data is changed.">
        Import, browsing, crates, and exports work without optional analysis tools. BPM and key analysis are separate opt-in workflows.
      </StatusStrip>
      {error && <StatusStrip tone="danger" role="alert" onDismiss={() => setError(null)}>{error}</StatusStrip>}
      {saved && <StatusStrip tone="good" onDismiss={() => setSaved(false)}>Saved the default export path mode in this library’s local app settings.</StatusStrip>}
      {analysisSaved && <StatusStrip tone="good" onDismiss={() => setAnalysisSaved(false)}>Saved optional analysis preferences. No analysis has run.</StatusStrip>}
      {librarySaved && <StatusStrip tone="good" onDismiss={() => setLibrarySaved(false)}>Saved the library root as pending. Restart CrateIQ to use it.</StatusStrip>}

      {loading ? <EmptyState title="Loading settings" message="Reading local runtime diagnostics…" /> : !settings ? <EmptyState title="Settings unavailable" message="Check that the local backend is running, then recheck readiness." /> : (
        <>
          <div className="settings-kpis">
            <KpiCard tone="cyan" icon={<FolderCog size={18} />} label="Library mode" value={settings.library.mode === 'demo' ? 'Demo' : 'Configured'} sub={settings.library.readiness_status.replace('_', ' ')} />
            <KpiCard tone="emerald" icon={<Wrench size={18} />} label="Optional tools" value={`${readyToolCount}/${settings.tools.length}`} sub="Only advanced workflows need them" />
            <KpiCard tone="violet" icon={<ShieldCheck size={18} />} label="Analysis safety" value="Locked" sub="MIK and existing values stay protected" />
          </div>

          <section className="section">
            <div className="card settings-card">
              <div className="card-title-row">
                <div><h2 className="card-title">Library</h2><p className="muted">Set a safe, existing library folder for the next CrateIQ start. Validation does not scan or import it.</p></div>
                <Badge tone={readiness === 'ready' ? 'succeeded' : 'pending'}>{readiness?.replace('_', ' ')}</Badge>
              </div>
              <dl className="def-list settings-def-list">
                <dt>Active library root</dt><dd><code>{settings.library.library_root}</code></dd>
                <dt>Pending library root</dt><dd><code>{settings.library.pending_library_root ?? 'None saved'}</code></dd>
                <dt>Library setup status</dt><dd>{setupInitialized ? 'Initialized' : !settings.library.pending_library_root ? 'Save a root first' : 'Needs initialization'}</dd>
                <dt>Processed database</dt><dd><code>{settings.library.processed_db}</code></dd>
                <dt>Manual Crates database</dt><dd><code>{settings.library.manual_crates_db}</code></dd>
                <dt>Exports root</dt><dd><code>{settings.library.exports_root}</code></dd>
              </dl>
              <div className="settings-preference">
                <label>Configured library root
                  <input className="form-input" value={libraryRootInput} onChange={(event) => { setLibraryRootInput(event.target.value); setLibraryValidation(null); setLibrarySaved(false) }} placeholder="/absolute/path/to/library" disabled={libraryBusy} />
                </label>
                <div className="settings-action-row">
                  <button className="btn btn--ghost" disabled={libraryBusy || !libraryRootInput.trim()} onClick={() => void validateRoot()}>{libraryBusy ? 'Validating…' : 'Validate folder'}</button>
                  <button className="btn btn--primary" disabled={libraryBusy || !libraryValidation?.valid} onClick={() => void saveLibraryRoot()}>Save pending root</button>
                </div>
              </div>
              {libraryValidation && <StatusStrip tone="good" icon={<CheckCircle2 size={15} />}>{libraryValidation.message}</StatusStrip>}
              {settings.library.restart_required && <StatusStrip tone="warn" icon={<CircleAlert size={15} />}>Restart required. The active root remains unchanged until CrateIQ is restarted.<br /><code>{settings.library.restart_command}</code></StatusStrip>}
              <p className="muted settings-note">The pending root is stored in this repository’s ignored local runtime config. No music files, tags, BPM, key, cue points, MIK values, or DJ application databases are changed.</p>
            </div>
          </section>

          <section className="section">
            <div className="card settings-card">
              <div className="card-title-row">
                <div><h2 className="card-title">Library setup &amp; import</h2><p className="muted">Each step is explicit. Initialization creates only CrateIQ’s local folders and empty index.</p></div>
                <Badge tone={setupInitialized ? 'succeeded' : 'pending'}>{setupInitialized ? 'Ready to preview' : 'Setup required'}</Badge>
              </div>
              <StatusStrip tone="info">Import will add tracks to CrateIQ’s local index only. BPM/key analysis is optional and can be run later; analysis tools are never run during import.</StatusStrip>
              {optionalAnalysisWarnings.length > 0 && (
                <StatusStrip tone="warn" icon={<CircleAlert size={15} />}>
                  {optionalAnalysisWarnings.join(' ')} Import without analysis is still available.
                </StatusStrip>
              )}
              <div className="settings-action-row">
                <button className="btn btn--primary" disabled={setupBusy || setupInitialized || !settings.library.pending_library_root} onClick={() => void runSetup('initialize')}>{setupBusy ? 'Working…' : 'Initialize library'}</button>
                <button className="btn btn--ghost" disabled={setupBusy || !setupInitialized} onClick={() => void runSetup('preview')}>Scan preview</button>
                <button className="btn btn--ghost" disabled={setupBusy || !setupResult?.track_count} onClick={() => void runSetup('import')}>Import previewed tracks</button>
              </div>
              {setupResult && <div className="settings-setup-result">
                <StatusStrip tone="good" icon={<CheckCircle2 size={15} />}>{setupResult.message}</StatusStrip>
                {typeof setupResult.track_count === 'number' && <p className="muted settings-note">Found {setupResult.track_count} supported audio file{setupResult.track_count === 1 ? '' : 's'}; showing up to {setupResult.sample_tracks?.length ?? 0} paths.</p>}
                {typeof setupResult.imported_count === 'number' && <p className="muted settings-note">Imported {setupResult.imported_count} track record{setupResult.imported_count === 1 ? '' : 's'} into the local index.</p>}
                {setupResult.sample_tracks && setupResult.sample_tracks.length > 0 && <ul className="settings-sample-list">{setupResult.sample_tracks.map((track) => <li key={track}><code>{track}</code></li>)}</ul>}
                {(setupResult.skipped_files?.length || setupResult.unsupported_files?.length || setupResult.warnings?.length) ? <StatusStrip tone="warn">{setupResult.warnings?.join(' ') || `${setupResult.unsupported_files?.length ?? 0} unsupported and ${setupResult.skipped_files?.length ?? 0} skipped file samples were omitted from import.`}</StatusStrip> : null}
              </div>}
            </div>
          </section>

          <section className="section" id="analysis-tools">
            <div className="card settings-card">
              <div className="card-title-row">
                <div><h2 className="card-title">Analysis &amp; tools</h2><p className="muted">Optional workflows stay separate from library setup. Saving these toggles never starts a job.</p></div>
                <Badge tone="info">Optional</Badge>
              </div>
              <div className="settings-core-status">
                {Object.entries(settings.capabilities.core).map(([key, capability]) => (
                  <span key={key}><CheckCircle2 size={13} /> {capability.purpose}</span>
                ))}
              </div>
              <div className="settings-analysis-preferences">
                <label className="form-check"><input type="checkbox" checked={analysis.analyze_bpm} onChange={(event) => setAnalysis((current) => ({ ...current, analyze_bpm: event.target.checked }))} disabled={analysisBusy} /> Analyze missing BPM</label>
                <label className="form-check"><input type="checkbox" checked={analysis.analyze_key} onChange={(event) => setAnalysis((current) => ({ ...current, analyze_key: event.target.checked }))} disabled={analysisBusy} /> Analyze missing key/Camelot</label>
                <label className="form-check"><input type="checkbox" checked={analysis.use_external_tools} onChange={(event) => setAnalysis((current) => ({ ...current, use_external_tools: event.target.checked }))} disabled={analysisBusy} /> Use external tools when an analysis workflow is explicitly run</label>
                <label className="form-check settings-policy-check"><input type="checkbox" checked={analysis.use_mik_when_present} disabled /> Use Mixed In Key metadata when present <Badge tone="succeeded">Locked</Badge></label>
                <label className="form-check settings-policy-check"><input type="checkbox" checked={analysis.preserve_existing_bpm_key_cues} disabled /> Preserve existing BPM, key, and cues <Badge tone="succeeded">Locked</Badge></label>
                <label className="form-check settings-policy-check"><input type="checkbox" checked={analysis.missing_data_only} disabled /> Missing-data-only analysis <Badge tone="succeeded">Locked</Badge></label>
              </div>
              <div className="settings-action-row">
                <button className="btn btn--primary" disabled={analysisBusy || !analysisChanged} onClick={() => void saveAnalysis()}>{analysisBusy ? 'Saving…' : 'Save analysis preferences'}</button>
              </div>
              <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>BPM/key analysis is default-off. Current in-app runners are intentionally not exposed here until a dedicated DB-only, missing-data-only analysis workflow is complete.</StatusStrip>
              <div className="settings-capability-list">
                <CapabilityCard label="Mixed In Key metadata coverage" capability={settings.capabilities.analysis.mixed_in_key_coverage} />
                <CapabilityCard label="BPM analysis" capability={settings.capabilities.analysis.bpm_analysis} />
                <CapabilityCard label="Key/Camelot analysis" capability={settings.capabilities.analysis.key_analysis} />
                <CapabilityCard label="Beets enrichment" capability={settings.capabilities.analysis.beets_enrichment} />
                <CapabilityCard label="Duplicate detection" capability={settings.capabilities.analysis.duplicate_detection} />
                <CapabilityCard label="Audio quality/probing" capability={settings.capabilities.analysis.audio_quality_probe} />
              </div>
            </div>
          </section>

          <section className="section">
            <div className="card settings-card">
              <div className="card-title-row"><div><h2 className="card-title">Preferences</h2><p className="muted">This export preference is independent of optional analysis tools.</p></div><Badge tone="info">Local preference</Badge></div>
              <div className="settings-preference">
                <label>Default export path mode
                  <select className="form-input" value={pathMode} onChange={(event) => setPathMode(event.target.value as typeof pathMode)} disabled={busy}>
                    <option value="filename">Filename (safe default)</option><option value="relative">Relative to library root</option><option value="absolute">Absolute path</option>
                  </select>
                </label>
                <button className="btn btn--primary" disabled={busy || pathMode === settings.preferences.default_export_path_mode} onClick={() => void savePreference()}>Save preference</button>
              </div>
              <p className="muted settings-note">Saved locally under the selected library. Existing export forms continue to require explicit preview and write actions.</p>
            </div>
          </section>

          <section className="section">
            <div className="card settings-card">
              <div className="card-title-row"><div><h2 className="card-title">Detected tools</h2><p className="muted">Availability is checked without invoking analysis or scanning tracks.</p></div><Badge tone="info">Runtime diagnostics</Badge></div>
              <div className="settings-tool-list">{settings.tools.map((tool) => <div className="settings-tool" key={tool.name}><div><strong>{tool.name}</strong><span>{tool.message}</span>{tool.resolved && <small>Source: {tool.source} · {tool.resolved}</small>}{!tool.resolved && <small>Configure with {tool.source} in a private environment if this optional workflow is needed.</small>}</div><Badge tone={badgeTone(tool.status)}>{tool.status}</Badge></div>)}</div>
            </div>
          </section>

          <section className="section">
            <div className="card settings-card">
              <div className="card-title-row"><div><h2 className="card-title">Safety policies</h2><p className="muted">These are deliberate product rules, not editable toggles.</p></div><Badge tone="succeeded">Locked</Badge></div>
              <ul className="settings-safety-list"><li><CheckCircle2 size={15} /> Mixed In Key remains authoritative for BPM, key, and cues.</li><li><CheckCircle2 size={15} /> Analysis fills missing data only; files and tags are never automatically modified.</li><li><CheckCircle2 size={15} /> Serato and Rekordbox live databases are never written by CrateIQ.</li><li><CheckCircle2 size={15} /> Export and apply workflows remain preview-first.</li></ul>
            </div>
          </section>

          <section className="section">
            <div className="card settings-card">
              <div className="card-title-row"><div><h2 className="card-title">Runtime diagnostics</h2><p className="muted">Read-only checks for the selected root, pipeline database, and optional tools.</p></div><a className="btn btn--ghost btn--xs" href="http://127.0.0.1:8020/api/runtime/readiness" target="_blank" rel="noreferrer">Open JSON</a></div>
              <StatusStrip tone={readiness === 'ready' ? 'good' : readiness === 'not_ready' ? 'danger' : 'warn'}>{readiness === 'ready' ? 'Local runtime is ready.' : readiness === 'not_ready' ? 'A required local runtime check needs attention.' : 'Local runtime is usable; optional workflow tools may need setup.'}</StatusStrip>
              <div className="settings-runtime-list">{runtime?.checks.map((check) => <div className="settings-runtime-check" key={check.name}><Badge tone={badgeTone(check.status)}>{check.status}</Badge><div><strong>{check.name.replace(/_/g, ' ')}</strong><span>{check.message}</span></div></div>)}</div>
              <p className="muted settings-note">Copyable readiness command: <code>curl -s http://127.0.0.1:8020/api/runtime/readiness | python3 -m json.tool</code></p>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
