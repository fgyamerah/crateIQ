import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  CheckCircle2,
  Clock,
  Eraser,
  FileCheck2,
  FolderInput,
  ListMusic,
  Lock,
  ScanSearch,
  Sparkles,
  Wrench,
} from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchSettings, importLibrary, scanLibraryPreview } from '../api/settings'
import { fetchLibraryOverview } from '../api/library'
import { fetchMetadataSanitationSummary } from '../api/metadataSanitation'
import { fetchMetadataRepairSummary } from '../api/metadataRepair'
import type { LibrarySetupResult, SettingsResponse } from '../types/settings'
import type { LibraryOverview } from '../api/library'
import type { MetadataSanitationSummary } from '../types/metadataSanitation'
import type { MetadataRepairSummary } from '../types/metadataRepair'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'
import Badge, { type BadgeTone } from '../components/ui/Badge'

type StepState = 'complete' | 'needs_review' | 'not_started' | 'unavailable'

const STATE_LABEL: Record<StepState, string> = {
  complete: 'Complete',
  needs_review: 'Needs review',
  not_started: 'Not started',
  unavailable: 'Not available yet',
}

const STATE_TONE: Record<StepState, BadgeTone> = {
  complete: 'succeeded',
  needs_review: 'pending',
  not_started: 'info',
  unavailable: 'info',
}

function StepIcon({ state }: { state: StepState }) {
  if (state === 'complete') return <CheckCircle2 size={16} className="prep-step-icon prep-step-icon--done" />
  if (state === 'unavailable') return <Lock size={16} className="prep-step-icon prep-step-icon--locked" />
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
    <section className={`prep-step${state === 'unavailable' ? ' prep-step--locked' : ''}`}>
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
  const [busy, setBusy] = useState<'preview' | 'import' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  const loadAll = useCallback(async () => {
    const [settingsResult, overviewResult, sanitationResult, repairResult] = await Promise.allSettled([
      fetchSettings(),
      fetchLibraryOverview(),
      fetchMetadataSanitationSummary(),
      fetchMetadataRepairSummary(),
    ])
    if (settingsResult.status === 'fulfilled') setSettings(settingsResult.value)
    if (overviewResult.status === 'fulfilled') setOverview(overviewResult.value)
    if (sanitationResult.status === 'fulfilled') setSanitation(sanitationResult.value)
    if (repairResult.status === 'fulfilled') setRepair(repairResult.value)
    const failedLabels: string[] = []
    if (settingsResult.status === 'rejected') failedLabels.push('settings')
    if (overviewResult.status === 'rejected') failedLabels.push('library overview')
    if (sanitationResult.status === 'rejected') failedLabels.push('sanitation summary')
    if (repairResult.status === 'rejected') failedLabels.push('repair summary')
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

        <PrepStep index={3} icon={<Sparkles size={16} />} title="Enrich" state="unavailable">
          <p className="prep-step-desc">
            Real Beets and MusicBrainz suggestions with field-by-field comparison land in the next cycle.
            The <Link to="/beets-review">Beets Review</Link> and <Link to="/enrichment-review">Enrichment Review</Link> pages
            exist today but do not yet source real provider candidates.
          </p>
        </PrepStep>

        <PrepStep index={4} icon={<ListMusic size={16} />} title="Review candidates" state="unavailable">
          <p className="prep-step-desc">Field-by-field candidate review becomes meaningful once real enrichment is available.</p>
        </PrepStep>

        <PrepStep index={5} icon={<FileCheck2 size={16} />} title="Apply to files" state="unavailable">
          <p className="prep-step-desc">
            Controlled, backed-up, confirmed writes to actual file tags are not implemented yet.
            No step in CrateIQ writes to your audio files today.
          </p>
        </PrepStep>

        <PrepStep index={6} icon={<Clock size={16} />} title="Analyze" state="unavailable">
          <p className="prep-step-desc">
            BPM/key analysis and waveform generation already work today from the{' '}
            <Link to="/jobs">Jobs</Link> page. They will be embedded directly in this workflow in a later cycle.
          </p>
        </PrepStep>

        <PrepStep index={7} icon={<CheckCircle2 size={16} />} title="Ready" state="unavailable">
          <p className="prep-step-desc">
            A conservative readiness score with blockers and warnings arrives in a later cycle. Until then, use{' '}
            <Link to="/crates">Manual Crates</Link> or <Link to="/smart-crates">Smart Crates</Link> directly once your
            library is clean.
          </p>
        </PrepStep>
      </div>
    </div>
  )
}
