import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  CheckCircle2,
  Clock,
  Eraser,
  FileCheck2,
  FolderInput,
  ListMusic,
  ScanSearch,
  Sparkles,
  Waves,
  Wrench,
} from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchSettings, importLibrary, scanLibraryPreview } from '../api/settings'
import { fetchLibraryOverview } from '../api/library'
import { fetchMetadataSanitationSummary } from '../api/metadataSanitation'
import { fetchMetadataRepairSummary } from '../api/metadataRepair'
import { previewAnalysisJob, runBpmAnalysis, runKeyAnalysis } from '../api/analysis'
import { fetchWaveformBulkPreview, startWaveformBulkGenerate } from '../api/waveformBulk'
import { fetchLibraryReadiness } from '../api/libraryReadiness'
import type { LibrarySetupResult, SettingsResponse } from '../types/settings'
import type { LibraryOverview } from '../api/library'
import type { MetadataSanitationSummary } from '../types/metadataSanitation'
import type { MetadataRepairSummary } from '../types/metadataRepair'
import type { AnalysisJobPreview } from '../types/analysis'
import type { WaveformBulkPreview } from '../types/waveformBulk'
import type { LibraryReadiness } from '../types/libraryReadiness'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'
import Badge, { type BadgeTone } from '../components/ui/Badge'

type StepState = 'complete' | 'needs_review' | 'not_started'

const READINESS_DISPLAY_CAP = 4

const STATE_LABEL: Record<StepState, string> = {
  complete: 'Complete',
  needs_review: 'Needs review',
  not_started: 'Not started',
}

const STATE_TONE: Record<StepState, BadgeTone> = {
  complete: 'succeeded',
  needs_review: 'pending',
  not_started: 'info',
}

function StepIcon({ state }: { state: StepState }) {
  if (state === 'complete') return <CheckCircle2 size={16} className="prep-step-icon prep-step-icon--done" />
  return <Clock size={16} className="prep-step-icon" />
}

function PrepStep({
  index,
  icon,
  title,
  state,
  children,
}: {
  index: number
  icon: ReactNode
  title: string
  state: StepState
  children: ReactNode
}) {
  return (
    <section className="prep-step">
      <div className="prep-step-header">
        <span className="prep-step-number">{index}</span>
        <span className="prep-step-title-icon">{icon}</span>
        <h2 className="prep-step-title">{title}</h2>
        <span className="prep-step-badge">
          <Badge tone={STATE_TONE[state]}>{STATE_LABEL[state]}</Badge>
        </span>
        <StepIcon state={state} />
      </div>
      <div className="prep-step-body">{children}</div>
    </section>
  )
}

export default function LibraryPrep() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null)
  const [overview, setOverview] = useState<LibraryOverview | null>(null)
  const [sanitation, setSanitation] = useState<MetadataSanitationSummary | null>(null)
  const [repair, setRepair] = useState<MetadataRepairSummary | null>(null)
  const [setupResult, setSetupResult] = useState<LibrarySetupResult | null>(null)
  const [bpmPreview, setBpmPreview] = useState<AnalysisJobPreview | null>(null)
  const [keyPreview, setKeyPreview] = useState<AnalysisJobPreview | null>(null)
  const [waveformPreview, setWaveformPreview] = useState<WaveformBulkPreview | null>(null)
  const [readiness, setReadiness] = useState<LibraryReadiness | null>(null)
  const [busy, setBusy] = useState<'preview' | 'import' | 'bpm' | 'key' | 'waveform' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [analyzeMessage, setAnalyzeMessage] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  const loadAll = useCallback(async () => {
    const [settingsResult, overviewResult, sanitationResult, repairResult, bpmResult, keyResult, waveformResult, readinessResult] =
      await Promise.allSettled([
        fetchSettings(),
        fetchLibraryOverview(),
        fetchMetadataSanitationSummary(),
        fetchMetadataRepairSummary(),
        previewAnalysisJob('bpm_analysis'),
        previewAnalysisJob('key_analysis'),
        fetchWaveformBulkPreview(),
        fetchLibraryReadiness(),
      ])
    if (settingsResult.status === 'fulfilled') setSettings(settingsResult.value)
    if (overviewResult.status === 'fulfilled') setOverview(overviewResult.value)
    if (sanitationResult.status === 'fulfilled') setSanitation(sanitationResult.value)
    if (repairResult.status === 'fulfilled') setRepair(repairResult.value)
    if (bpmResult.status === 'fulfilled') setBpmPreview(bpmResult.value)
    if (keyResult.status === 'fulfilled') setKeyPreview(keyResult.value)
    if (waveformResult.status === 'fulfilled') setWaveformPreview(waveformResult.value)
    if (readinessResult.status === 'fulfilled') setReadiness(readinessResult.value)
    const failedLabels: string[] = []
    if (settingsResult.status === 'rejected') failedLabels.push('settings')
    if (overviewResult.status === 'rejected') failedLabels.push('library overview')
    if (sanitationResult.status === 'rejected') failedLabels.push('sanitation summary')
    if (repairResult.status === 'rejected') failedLabels.push('repair summary')
    if (bpmResult.status === 'rejected') failedLabels.push('BPM analysis status')
    if (keyResult.status === 'rejected') failedLabels.push('key analysis status')
    if (waveformResult.status === 'rejected') failedLabels.push('waveform status')
    if (readinessResult.status === 'rejected') failedLabels.push('readiness')
    setLoadError(
      failedLabels.length
        ? `Could not load ${failedLabels.join(', ')} — status shown below may be incomplete.`
        : null,
    )
    setLoaded(true)
  }, [])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  const runScan = async () => {
    setBusy('preview')
    setError(null)
    try {
      const result = await scanLibraryPreview()
      setSetupResult(result)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Scan preview failed.')
    } finally {
      setBusy(null)
    }
  }

  const runImport = async () => {
    setBusy('import')
    setError(null)
    try {
      const result = await importLibrary()
      setSetupResult(result)
      await loadAll()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Import failed.')
    } finally {
      setBusy(null)
    }
  }

  const runBpm = async () => {
    if (!bpmPreview?.candidate_count) return
    setBusy('bpm')
    setError(null)
    setAnalyzeMessage(null)
    try {
      const result = await runBpmAnalysis(bpmPreview.candidate_count)
      setAnalyzeMessage(`BPM analysis: ${result.updated} updated, ${result.skipped} skipped, ${result.failed} failed.`)
      await loadAll()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'BPM analysis failed.')
    } finally {
      setBusy(null)
    }
  }

  const runKey = async () => {
    if (!keyPreview?.candidate_count) return
    setBusy('key')
    setError(null)
    setAnalyzeMessage(null)
    try {
      const result = await runKeyAnalysis(keyPreview.candidate_count)
      setAnalyzeMessage(`Key analysis: ${result.updated} updated, ${result.skipped} skipped, ${result.failed} failed.`)
      await loadAll()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Key analysis failed.')
    } finally {
      setBusy(null)
    }
  }

  const runWaveforms = async () => {
    if (!waveformPreview?.eligible_to_generate) return
    setBusy('waveform')
    setError(null)
    setAnalyzeMessage(null)
    try {
      const result = await startWaveformBulkGenerate()
      setAnalyzeMessage(`Waveform generation started for ${result.eligible_total} track(s) — track progress on the Jobs page.`)
      await loadAll()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Waveform generation failed to start.')
    } finally {
      setBusy(null)
    }
  }

  const libraryInitialized = settings?.library.library_initialized ?? false
  const totalTracks = overview?.total_tracks ?? 0
  const importState: StepState = totalTracks > 0 ? 'complete' : libraryInitialized ? 'needs_review' : 'not_started'

  const sanitationPending = sanitation?.pending_count ?? 0
  const repairPending = repair?.pending_count ?? 0
  const cleanupQueueTotal = (sanitation?.queue_total ?? 0) + (repair?.queue_total ?? 0)
  const cleanState: StepState =
    totalTracks === 0 ? 'not_started' : sanitationPending + repairPending > 0 ? 'needs_review' : 'complete'

  const previewResult =
    setupResult && typeof setupResult.supported_audio_files === 'number' && typeof setupResult.imported_count !== 'number'
      ? setupResult
      : null
  const importResult = setupResult && typeof setupResult.imported_count === 'number' ? setupResult : null

  const analyzeOutstanding = (bpmPreview?.candidate_count ?? 0) + (keyPreview?.candidate_count ?? 0) + (waveformPreview?.eligible_to_generate ?? 0)
  const analyzeState: StepState =
    totalTracks === 0 ? 'not_started' : analyzeOutstanding > 0 ? 'needs_review' : 'complete'

  const readyState: StepState = !readiness ? 'not_started' : readiness.ready ? 'complete' : 'needs_review'

  return (
    <div className="page library-prep-page">
      <PageHeader
        title="Library Prep"
        subtitle="One workflow from importing a folder to a DJ-ready library — import, clean metadata, enrich, apply to files, and analyze."
      />

      {!loaded && <StatusStrip tone="info">Loading library prep status…</StatusStrip>}
      {loadError && <StatusStrip tone="warn">{loadError}</StatusStrip>}
      {error && <StatusStrip tone="danger">{error}</StatusStrip>}

      <div className="prep-steps">
        <PrepStep index={1} icon={<FolderInput size={16} />} title="Import" state={importState}>
          <p className="prep-step-desc">
            Configure the library root in <Link to="/settings">Settings</Link>, then scan and import from here.
            Import reads filenames and embedded file tags to build CrateIQ's local index —
            it never modifies your music files.
          </p>
          <StatusStrip tone="info">Local index only — your audio files and tags are never changed by import.</StatusStrip>
          <div className="prep-step-actions">
            <button className="btn btn--ghost btn--sm" disabled={busy !== null || !libraryInitialized} onClick={() => void runScan()}>
              {busy === 'preview' ? 'Scanning…' : <><ScanSearch size={14} /> Scan preview</>}
            </button>
            <button
              className="btn btn--primary btn--sm"
              disabled={busy !== null || !libraryInitialized || !(previewResult?.importable ?? importResult?.importable)}
              onClick={() => void runImport()}
            >
              {busy === 'import' ? 'Importing…' : 'Import tracks'}
            </button>
            {!libraryInitialized && <span className="prep-step-hint">Initialize a library root in Settings first.</span>}
          </div>
          {previewResult && (
            <p className="prep-step-result">
              Found {previewResult.supported_audio_files} supported audio file(s) of {previewResult.total_files} discovered
              {previewResult.unsupported_file_count ? `, ${previewResult.unsupported_file_count} unsupported` : ''}
              {previewResult.skipped_file_count ? `, ${previewResult.skipped_file_count} skipped` : ''}. Not imported yet.
            </p>
          )}
          {importResult && (
            <p className="prep-step-result">
              Imported {importResult.imported_count} new track(s); {importResult.existing_count ?? 0} already indexed.
              Embedded tags were read for {importResult.tags_read_count ?? 0} file(s). Total indexed: {importResult.total_indexed_count}.
            </p>
          )}
          {totalTracks > 0 && (
            <p className="prep-step-result">{totalTracks} track(s) currently in the local index.</p>
          )}
        </PrepStep>

        <PrepStep index={2} icon={<Eraser size={16} />} title="Clean metadata" state={cleanState}>
          <p className="prep-step-desc">
            Review sanitation and repair suggestions for junk tokens, weak filename parses, and suspicious
            artist/title values. Approving a suggestion updates CrateIQ's local index only — file tags are unchanged.
          </p>
          <StatusStrip tone="info">Local index only — file tags unchanged. File write-back arrives in a later step.</StatusStrip>
          <div className="prep-step-links">
            <Link className="btn btn--ghost btn--sm" to="/metadata-sanitation">
              <Wrench size={14} /> Metadata Sanitation {sanitation ? `(${sanitation.pending_count} pending)` : ''}
            </Link>
            <Link className="btn btn--ghost btn--sm" to="/metadata-repair">
              <Wrench size={14} /> Metadata Repair {repair ? `(${repair.pending_count} pending)` : ''}
            </Link>
          </div>
          {totalTracks > 0 && cleanupQueueTotal === 0 && (
            <p className="prep-step-result">No sanitation or repair proposals generated yet — open a queue to generate them.</p>
          )}
        </PrepStep>

        <PrepStep index={3} icon={<Sparkles size={16} />} title="Enrich & review candidates" state="not_started">
          <p className="prep-step-desc">
            Open a track in <Link to="/enrichment-review">Enrichment Review</Link> and click "Look up on Beets" or
            "Look up on MusicBrainz" for a real, bounded, per-track lookup — never automatic, never a whole-library scan.
            The same page's field-by-field table then compares Current, Beets, MusicBrainz, and local-tag values side by
            side — select which source wins per field, then save to CrateIQ's local index only.
          </p>
          <div className="prep-step-links">
            <Link className="btn btn--ghost btn--sm" to="/enrichment-review">
              <Sparkles size={14} /> Open Enrichment Review
            </Link>
          </div>
        </PrepStep>

        <PrepStep index={4} icon={<FileCheck2 size={16} />} title="Apply to files" state="not_started">
          <p className="prep-step-desc">
            Write approved artist/title/album/genre values to real file tags — the only step in CrateIQ
            that modifies your audio files. Every write is backed up first and can be restored.
          </p>
          <div className="prep-step-actions">
            <Link className="btn btn--primary btn--sm" to="/apply-to-files">
              <FileCheck2 size={14} /> Open Apply to Files
            </Link>
          </div>
        </PrepStep>

        <PrepStep index={5} icon={<Activity size={16} />} title="Analyze" state={analyzeState}>
          <p className="prep-step-desc">
            Launch BPM/key analysis and waveform generation directly from here, or track full history and cancel
            running jobs on the <Link to="/jobs">Jobs</Link> page.
          </p>
          {analyzeMessage && <StatusStrip tone="good">{analyzeMessage}</StatusStrip>}
          <div className="prep-step-actions">
            <button className="btn btn--ghost btn--sm" disabled={busy !== null || !bpmPreview?.candidate_count} onClick={() => void runBpm()}>
              <Activity size={14} /> {busy === 'bpm' ? 'Analyzing…' : `Analyze missing BPM (${bpmPreview?.candidate_count ?? 0} pending)`}
            </button>
            <button className="btn btn--ghost btn--sm" disabled={busy !== null || !keyPreview?.candidate_count} onClick={() => void runKey()}>
              <Activity size={14} /> {busy === 'key' ? 'Analyzing…' : `Analyze missing key (${keyPreview?.candidate_count ?? 0} pending)`}
            </button>
            <button className="btn btn--ghost btn--sm" disabled={busy !== null || !waveformPreview?.eligible_to_generate} onClick={() => void runWaveforms()}>
              <Waves size={14} /> {busy === 'waveform' ? 'Starting…' : `Generate missing waveforms (${waveformPreview?.eligible_to_generate ?? 0} pending)`}
            </button>
          </div>
          {totalTracks > 0 && analyzeOutstanding === 0 && (
            <p className="prep-step-result">BPM, key, and waveform coverage are complete for this library.</p>
          )}
        </PrepStep>

        <PrepStep index={6} icon={<CheckCircle2 size={16} />} title="Ready" state={readyState}>
          <p className="prep-step-desc">{readiness?.message ?? 'Readiness status will appear once tracks are imported.'}</p>
          {readiness && readiness.blockers.length > 0 && (
            <div className="prep-step-links">
              {readiness.blockers.slice(0, READINESS_DISPLAY_CAP).map((reason) => (
                <StatusStrip key={reason.code} tone="danger">{reason.message}</StatusStrip>
              ))}
              {readiness.blockers.length > READINESS_DISPLAY_CAP && (
                <p className="prep-step-result">+{readiness.blockers.length - READINESS_DISPLAY_CAP} more blocker(s).</p>
              )}
            </div>
          )}
          {readiness && readiness.warnings.length > 0 && (
            <div className="prep-step-links">
              {readiness.warnings.slice(0, READINESS_DISPLAY_CAP).map((reason) => (
                <StatusStrip key={reason.code} tone="warn">{reason.message}</StatusStrip>
              ))}
              {readiness.warnings.length > READINESS_DISPLAY_CAP && (
                <p className="prep-step-result">+{readiness.warnings.length - READINESS_DISPLAY_CAP} more warning(s).</p>
              )}
            </div>
          )}
          <div className="prep-step-links">
            <Link className="btn btn--primary btn--sm" to="/crates">
              <ListMusic size={14} /> Continue to Manual Crates
            </Link>
            <Link className="btn btn--ghost btn--sm" to="/smart-crates">
              <Sparkles size={14} /> Continue to Smart Crates
            </Link>
          </div>
        </PrepStep>
      </div>
    </div>
  )
}
