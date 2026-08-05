import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  ArrowRight,
  ArchiveRestore,
  ChartNoAxesColumnIncreasing,
  CheckCircle2,
  CircleAlert,
  Database,
  FolderCog,
  HardDrive,
  ListChecks,
  RefreshCw,
  ScanSearch,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Wrench,
} from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchMikCoverage, importMikMetadata, previewMikMetadata } from '../api/analysis'
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
import type { MikCoverageResult, MikImportResult } from '../types/analysis'
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

function CapabilityCard({ label, capability, jobRoute }: { label: string; capability: WorkflowCapability; jobRoute?: string }) {
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
      {jobRoute ? <Link className="btn btn--ghost btn--xs" to={jobRoute}>{capability.action_state === 'setup_required' ? 'Tool setup' : 'Open jobs'}</Link> : <button className="btn btn--ghost btn--xs" type="button" disabled>{actionLabel}</button>}
    </div>
  )
}

function SettingsMetric({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return <div className="settings-metric"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>
}

export default function Settings() {
  const location = useLocation()
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
  const [previewedAt, setPreviewedAt] = useState<Date | null>(null)
  const [mikCoverage, setMikCoverage] = useState<MikCoverageResult | null>(null)
  const [mikPreview, setMikPreview] = useState<MikCoverageResult | null>(null)
  const [mikImport, setMikImport] = useState<MikImportResult | null>(null)
  const [mikBusy, setMikBusy] = useState(false)

  const applySettings = (next: SettingsResponse) => {
    setSettings(next)
    setPathMode(next.preferences.default_export_path_mode)
    setAnalysis(next.preferences.analysis)
  }

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [nextSettings, nextRuntime, nextMikCoverage] = await Promise.all([
        fetchSettings(),
        fetchSettingsRuntime(),
        fetchMikCoverage().catch(() => null),
      ])
      applySettings(nextSettings)
      setRuntime(nextRuntime)
      setMikCoverage(nextMikCoverage)
    } catch (err) {
      setError(errorMessage(err, 'Could not load local settings.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (loading || !location.hash) return
    document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: 'start' })
  }, [loading, location.hash])

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

  const runMikPreview = async () => {
    setMikBusy(true)
    setError(null)
    try {
      const result = await previewMikMetadata()
      setMikPreview(result)
      setMikImport(null)
    } catch (err) {
      setError(errorMessage(err, 'Could not preview MIK-compatible metadata.'))
    } finally {
      setMikBusy(false)
    }
  }

  const runMikImport = async () => {
    setMikBusy(true)
    setError(null)
    try {
      const result = await importMikMetadata()
      setMikImport(result)
      setMikCoverage(result)
      setMikPreview(null)
    } catch (err) {
      setError(errorMessage(err, 'Could not import MIK-compatible metadata into the local index.'))
    } finally {
      setMikBusy(false)
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
      if (action === 'preview') setPreviewedAt(new Date())
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
  const previewResult = setupResult && typeof setupResult.supported_audio_files === 'number' && typeof setupResult.imported_count !== 'number'
    ? setupResult
    : null
  const importResult = setupResult && typeof setupResult.imported_count === 'number'
    ? setupResult
    : null
  const canInitialize = Boolean(settings && !setupInitialized && !settings.library.restart_required)
  const canPreview = Boolean(settings && setupInitialized && !settings.library.restart_required)
  const canImport = Boolean(previewResult?.importable)
  const mikResult = mikImport ?? mikPreview ?? mikCoverage

  return (
    <div className="page settings-page">
      <PageHeader
        title="Settings"
        subtitle="Set up the local library, then choose optional analysis only when you need it."
        actions={(
          <div className="settings-header-actions">
            <Link className="btn btn--ghost btn--sm" to="/">Open Library</Link>
            <Link className="btn btn--ghost btn--sm" to="/jobs">Analysis Jobs</Link>
            <Link className="btn btn--ghost btn--sm" to="/exports">Exports</Link>
            <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void recheck()}>
              <RefreshCw size={13} className={busy ? 'spin' : ''} /> Recheck
            </button>
          </div>
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
          <nav className="settings-tabs" aria-label="Settings sections">
            <a href="#library-paths">Library &amp; Paths</a>
            <a href="#analysis-tools">Analysis &amp; Tools</a>
            <a href="#safety-behavior">Safety &amp; Behavior</a>
            <a href="#job-defaults">Job Defaults</a>
            <a href="#backup-restore">Backup &amp; Restore</a>
            <a href="#diagnostics">Diagnostics</a>
          </nav>
          <div className="settings-kpis">
            <KpiCard tone="cyan" icon={<FolderCog size={18} />} label="Library mode" value={settings.library.mode === 'demo' ? 'Demo' : 'Configured'} sub={settings.library.readiness_status.replace('_', ' ')} />
            <KpiCard tone="emerald" icon={<Wrench size={18} />} label="Optional tools" value={`${readyToolCount}/${settings.tools.length}`} sub="Only advanced workflows need them" />
            <KpiCard tone="violet" icon={<ShieldCheck size={18} />} label="Analysis safety" value="Locked" sub="MIK and existing values stay protected" />
          </div>

          <section className="section" id="library-paths">
            <div className="card settings-card">
              <div className="card-title-row">
                <div><h2 className="card-title"><FolderCog size={16} /> Library &amp; paths</h2><p className="muted">Step 1: set a safe, existing library folder for the next CrateIQ start. Validation does not scan or import it.</p></div>
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
              <div className="settings-library-metrics">
                <SettingsMetric label="Index state" value={setupInitialized ? 'Ready' : 'Setup'} detail={setupInitialized ? 'Local index is available' : 'Initialize before scanning'} />
                <SettingsMetric label="Indexed tracks" value={mikCoverage?.summary.total_tracks ?? '—'} detail="Current local-index coverage" />
                <SettingsMetric label="Export path" value={pathMode} detail="Safe local preference" />
              </div>
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

          <section className="section settings-anchor-section" id="library-setup-import">
            <div className="card settings-card settings-import-wizard">
              <div className="card-title-row">
                <div><h2 className="card-title">Library setup &amp; import</h2><p className="muted">Follow the steps in order. Optional BPM/key analysis always happens later, never during import.</p></div>
                <Badge tone={importResult ? 'succeeded' : previewResult ? 'info' : setupInitialized ? 'succeeded' : 'pending'}>{importResult ? 'Imported' : previewResult ? 'Preview ready' : setupInitialized ? 'Ready to preview' : 'Setup required'}</Badge>
              </div>
              <StatusStrip tone="info">Import will add tracks to CrateIQ’s local index only. BPM/key analysis is optional and can be run later; analysis tools are never run during import.</StatusStrip>
              {optionalAnalysisWarnings.length > 0 && (
                <StatusStrip tone="warn" icon={<CircleAlert size={15} />}>
                  {optionalAnalysisWarnings.join(' ')} Import without analysis is still available.
                </StatusStrip>
              )}
              <ol className="settings-import-steps">
                <li className={setupInitialized ? 'is-complete' : ''}>
                  <span className="settings-step-number">2</span>
                  <div className="settings-step-content">
                    <div><strong>Initialize CrateIQ index</strong><p>Creates only <code>logs/processed.db</code> and the local exports folder. No scan and no file changes.</p></div>
                    <button className="btn btn--primary btn--sm" disabled={setupBusy || !canInitialize} onClick={() => void runSetup('initialize')} title={settings.library.restart_required ? 'Restart CrateIQ before initializing the pending library root.' : undefined}>{setupBusy ? 'Working…' : setupInitialized ? 'Index ready' : 'Initialize index'}</button>
                  </div>
                </li>
                <li className={previewResult ? 'is-complete' : ''}>
                  <span className="settings-step-number">3</span>
                  <div className="settings-step-content">
                    <div><strong>Scan preview</strong><p>Read-only discovery of <code>.mp3 .wav .aiff .aif .flac .m4a .aac .ogg</code> files, including subfolders.</p>{previewedAt && <small>Last preview: {previewedAt.toLocaleTimeString()}</small>}</div>
                    <button className="btn btn--ghost btn--sm" disabled={setupBusy || !canPreview} onClick={() => void runSetup('preview')} title={!canPreview ? 'Initialize the active library root before scanning.' : undefined}>{setupBusy ? 'Scanning…' : <><ScanSearch size={14} /> Scan preview</>}</button>
                  </div>
                </li>
                <li className={previewResult ? 'is-complete' : ''}>
                  <span className="settings-step-number">4</span>
                  <div className="settings-step-content">
                    <div><strong>Review preview results</strong><p>Check the supported-file count, samples, and any warnings below. Nothing has been indexed yet.</p></div>
                    <Badge tone={previewResult ? 'succeeded' : 'pending'}>{previewResult ? 'Ready to import' : 'Waiting for preview'}</Badge>
                  </div>
                </li>
                <li className={importResult ? 'is-complete' : ''}>
                  <span className="settings-step-number">5</span>
                  <div className="settings-step-content">
                    <div><strong>Import previewed tracks</strong><p>Writes only to CrateIQ’s local index. Music files, tags, BPM, key, cues, and MIK data stay unchanged.</p></div>
                    <button className="btn btn--primary btn--sm" disabled={setupBusy || !canImport} onClick={() => void runSetup('import')} title={!canImport ? 'Run a preview that finds supported audio before importing.' : undefined}>{setupBusy ? 'Importing…' : 'Import tracks'}</button>
                  </div>
                </li>
              </ol>
              {previewResult && <div className="settings-setup-result">
                <div className="settings-import-result-head"><div><h3>Preview results</h3><p className="muted">Review this read-only discovery before indexing anything.</p></div><Badge tone={previewResult.importable ? 'succeeded' : 'pending'}>{previewResult.importable ? 'Importable' : 'No audio found'}</Badge></div>
                <div className="settings-import-summary">
                  <span><strong>{previewResult.supported_audio_files}</strong> supported audio</span>
                  <span><strong>{previewResult.total_files}</strong> files checked</span>
                  <span><strong>{previewResult.folders_scanned}</strong> folders scanned</span>
                  <span><strong>{previewResult.unsupported_file_count ?? 0}</strong> unsupported</span>
                  <span><strong>{previewResult.skipped_file_count ?? 0}</strong> skipped</span>
                </div>
                {!previewResult.importable ? <EmptyState icon={<ScanSearch size={22} />} title="No audio files found" message="Choose another folder or add supported audio, then run another read-only preview." /> : <>
                  {previewResult.sample_tracks?.length ? <div className="settings-import-samples"><strong>Sample tracks</strong><ul>{previewResult.sample_tracks.map((track) => <li key={track}><code>{track}</code></li>)}</ul></div> : null}
                  {previewResult.duplicate_name_candidates?.length ? <StatusStrip tone="warn">Possible duplicate filenames: {previewResult.duplicate_name_candidates.map((candidate) => `${candidate.filename} (${candidate.count})`).join(', ')}. This is a review hint only; no duplicate detection has run.</StatusStrip> : null}
                  {previewResult.long_path_warnings?.length ? <StatusStrip tone="warn">Long paths to review: {previewResult.long_path_warnings.join(', ')}</StatusStrip> : null}
                </>}
                {(previewResult.warnings?.length || previewResult.unsupported_files?.length || previewResult.skipped_files?.length) ? <StatusStrip tone="warn">{previewResult.warnings?.join(' ') || `${previewResult.unsupported_file_count ?? 0} unsupported and ${previewResult.skipped_file_count ?? 0} skipped files were omitted from import.`}</StatusStrip> : null}
              </div>}
              {importResult && <div className="settings-setup-result">
                <StatusStrip tone="good" icon={<CheckCircle2 size={15} />}>{importResult.message}</StatusStrip>
                <div className="settings-import-summary">
                  <span><strong>{importResult.imported_count}</strong> newly indexed</span>
                  <span><strong>{importResult.existing_count ?? 0}</strong> already indexed</span>
                  <span><strong>{importResult.total_indexed_count ?? 0}</strong> total indexed</span>
                  <span><strong>{importResult.skipped_file_count ?? 0}</strong> skipped</span>
                </div>
                {importResult.warnings?.length ? <StatusStrip tone="warn">{importResult.warnings.join(' ')}</StatusStrip> : null}
                <div className="settings-next-actions">
                  {(importResult.next_actions ?? [
                    { label: 'Open Library', route: '/' },
                    { label: 'Review Issues', route: '/issues' },
                    { label: 'Build Manual Crates', route: '/crates' },
                    { label: 'Configure Optional Analysis', route: '/settings#analysis-tools' },
                  ]).map((action) => <Link className="btn btn--ghost btn--sm" key={action.route} to={action.route}>{action.label}<ArrowRight size={13} /></Link>)}
                </div>
              </div>}
            </div>
          </section>

          <section className="section settings-anchor-section" id="analysis-tools">
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
              <StatusStrip tone="info" icon={<ShieldCheck size={15} />}>BPM/key analysis is default-off. Open Analysis Jobs to preview and explicitly confirm supported DB-only workflows.</StatusStrip>
              <div className="settings-mik-panel">
                <div className="settings-import-result-head"><div><h3>Mixed In Key coverage</h3><p className="muted">Mixed In Key is a trusted metadata input source, not a required executable.</p></div><Badge tone={settings.capabilities.analysis.mixed_in_key_coverage.available ? 'succeeded' : 'pending'}>{settings.capabilities.analysis.mixed_in_key_coverage.status}</Badge></div>
                <StatusStrip tone="info">Mixed In Key-compatible values are treated as trusted when present. Preview reads existing tags; import writes only CrateIQ’s local index. Music files and tags are not changed.</StatusStrip>
                {mikResult && <>
                  <div className="settings-import-summary settings-mik-summary">
                    <span><strong>{mikResult.summary.with_bpm}</strong> with BPM</span>
                    <span><strong>{mikResult.summary.with_key}</strong> with key</span>
                    <span><strong>{mikResult.summary.with_camelot}</strong> with Camelot</span>
                    <span><strong>{mikResult.summary.fallback_bpm_candidates}</strong> BPM fallback candidates</span>
                    <span><strong>{mikResult.summary.fallback_key_candidates}</strong> key fallback candidates</span>
                  </div>
                  <p className="muted settings-note">Cue support: <strong>{mikResult.cue_support}</strong>. {mikResult.summary.with_cues} tracks with detectable cues.</p>
                  {mikResult.samples.length > 0 && <div className="settings-import-samples"><strong>Detected tag samples</strong><ul>{mikResult.samples.map((sample) => <li key={sample.track_id}><code>{sample.filename}</code> · {sample.bpm ?? '—'} BPM · {sample.key_camelot ?? sample.key_musical ?? '—'} · trusted {sample.source}</li>)}</ul></div>}
                  {mikResult.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}
                </>}
                <div className="settings-action-row">
                  <button className="btn btn--ghost btn--sm" disabled={mikBusy} onClick={() => void runMikPreview()}>{mikBusy ? 'Reading tags…' : 'Preview MIK metadata'}</button>
                  <button className="btn btn--primary btn--sm" disabled={mikBusy || !mikPreview?.samples.length} onClick={() => void runMikImport()} title={!mikPreview?.samples.length ? 'Preview detected MIK-compatible tags before importing them.' : undefined}>{mikBusy ? 'Importing…' : 'Import trusted metadata'}</button>
                </div>
                {mikImport && <StatusStrip tone="good" icon={<CheckCircle2 size={15} />}>Imported {mikImport.imported_count} track{mikImport.imported_count === 1 ? '' : 's'} into the local index; {mikImport.unchanged_count} existing track{mikImport.unchanged_count === 1 ? '' : 's'} stayed unchanged.</StatusStrip>}
                <div className="settings-next-actions"><Link className="btn btn--ghost btn--xs" to="/jobs#mixed-in-key-coverage">Open Analysis Jobs <ArrowRight size={13} /></Link><Link className="btn btn--ghost btn--xs" to="/smart-crates">Build Smart Crates <ArrowRight size={13} /></Link></div>
              </div>
              <div className="settings-capability-list">
                <CapabilityCard label="BPM analysis" capability={settings.capabilities.analysis.bpm_analysis} jobRoute="/jobs#bpm-analysis" />
                <CapabilityCard label="Key/Camelot analysis" capability={settings.capabilities.analysis.key_analysis} jobRoute="/jobs#key-analysis" />
                <CapabilityCard label="Beets enrichment" capability={settings.capabilities.analysis.beets_enrichment} jobRoute="/jobs#beets-enrichment" />
                <CapabilityCard label="Duplicate detection" capability={settings.capabilities.analysis.duplicate_detection} jobRoute="/jobs#duplicate-detection" />
                <CapabilityCard label="Audio quality/probing" capability={settings.capabilities.analysis.audio_quality_probe} jobRoute="/jobs#audio-quality-probe" />
              </div>
            </div>
          </section>

          <section className="section settings-anchor-section" id="job-defaults">
            <div className="card settings-card">
              <div className="card-title-row"><div><h2 className="card-title"><SlidersHorizontal size={16} /> Job defaults</h2><p className="muted">Safe preferences only. They never start analysis or change music files.</p></div><Badge tone="info">Local preference</Badge></div>
              <div className="settings-job-defaults">
                <SettingsMetric label="BPM analysis" value={analysis.analyze_bpm ? 'Enabled' : 'Off'} detail="Explicit job only" />
                <SettingsMetric label="Key/Camelot" value={analysis.analyze_key ? 'Enabled' : 'Off'} detail="Explicit job only" />
                <SettingsMetric label="Missing data only" value="Locked" detail="Never overwrites trusted values" />
              </div>
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

          <section className="section settings-anchor-section" id="safety-behavior">
            <div className="card settings-card">
              <div className="card-title-row"><div><h2 className="card-title"><ShieldCheck size={16} /> Safety &amp; behavior</h2><p className="muted">These are deliberate product rules, not editable toggles.</p></div><Badge tone="succeeded">Locked</Badge></div>
              <div className="settings-policy-grid">
                <span><ShieldCheck size={14} /> No tag writes</span><span><ShieldCheck size={14} /> No music-file modifications</span><span><ShieldCheck size={14} /> Preserve MIK metadata</span><span><ShieldCheck size={14} /> Missing data only</span><span><ShieldCheck size={14} /> No live Serato writes</span><span><ShieldCheck size={14} /> No live Rekordbox writes</span>
              </div>
              <ul className="settings-safety-list"><li><CheckCircle2 size={15} /> Mixed In Key remains authoritative for BPM, key, and cues.</li><li><CheckCircle2 size={15} /> Analysis fills missing data only; files and tags are never automatically modified.</li><li><CheckCircle2 size={15} /> Serato and Rekordbox live databases are never written by CrateIQ.</li><li><CheckCircle2 size={15} /> Export and apply workflows remain preview-first.</li></ul>
            </div>
          </section>

          <section className="section settings-anchor-section" id="backup-restore">
            <div className="card settings-card settings-maintenance-card">
              <div className="card-title-row"><div><h2 className="card-title"><ArchiveRestore size={16} /> Backup &amp; restore</h2><p className="muted">Local index maintenance is planned as a separate, review-first workflow.</p></div><Badge tone="pending">Planned</Badge></div>
              <div className="settings-maintenance-grid">
                <div><Database size={17} /><strong>Local index</strong><span>CrateIQ stores imported track records in the selected local index.</span></div>
                <div><HardDrive size={17} /><strong>Export workspace</strong><span>Exports remain in the configured local exports folder.</span></div>
                <div><ListChecks size={17} /><strong>No restore action yet</strong><span>Backup, restore, and cleanup controls are intentionally not exposed until their safety contract is complete.</span></div>
              </div>
            </div>
          </section>

          <section className="section settings-anchor-section" id="diagnostics">
            <div className="card settings-card">
              <div className="card-title-row"><div><h2 className="card-title"><ChartNoAxesColumnIncreasing size={16} /> Diagnostics &amp; tool detection</h2><p className="muted">Availability is checked without invoking analysis or scanning tracks.</p></div><Badge tone="info">Runtime diagnostics</Badge></div>
              <div className="settings-tool-list">{settings.tools.map((tool) => <div className="settings-tool" key={tool.name}><div><strong>{tool.name}</strong><span>{tool.message}</span>{tool.resolved && <small>Source: {tool.source} · {tool.resolved}</small>}{!tool.resolved && <small>Configure with {tool.source} in a private environment if this optional workflow is needed.</small>}</div><Badge tone={badgeTone(tool.status)}>{tool.status}</Badge></div>)}</div>
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
