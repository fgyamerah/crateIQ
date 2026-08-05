import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Lightbulb, Loader2, Save, WandSparkles } from 'lucide-react'
import { ApiError } from '../api/client'
import { fetchSmartCratePresets, previewSmartCrate, saveSmartCrate } from '../api/smartCrates'
import type { SmartCrateCriteria, SmartCratePreset, SmartCratePreview } from '../types/smartCrate'
import Badge from '../components/ui/Badge'
import EmptyState from '../components/ui/EmptyState'
import KpiCard from '../components/ui/KpiCard'
import PageHeader from '../components/PageHeader'
import StatusStrip from '../components/ui/StatusStrip'
import AudioPreviewPlayer, { type AudioPreviewTrack } from '../components/player/AudioPreviewPlayer'

const initialCriteria: SmartCrateCriteria = { name: 'Smart Crate', genres: [], issue_free_only: true, limit: 25 }
const titleOf = (track: { artist: string | null; title: string | null; filename: string | null }) => [track.artist, track.title].filter(Boolean).join(' — ') || track.filename || 'Untitled track'
const numberOrNull = (value: string) => value.trim() ? Number(value) : null

export default function SmartCrates() {
  const [presets, setPresets] = useState<SmartCratePreset[]>([])
  const [criteria, setCriteria] = useState<SmartCrateCriteria>(initialCriteria)
  const [preview, setPreview] = useState<SmartCratePreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedName, setSavedName] = useState<string | null>(null)
  const [previewTrack, setPreviewTrack] = useState<AudioPreviewTrack | null>(null)
  useEffect(() => { fetchSmartCratePresets().then(setPresets).catch((err) => setError(err instanceof ApiError ? err.displayMessage : 'Could not load presets.')).finally(() => setLoading(false)) }, [])
  const update = (patch: Partial<SmartCrateCriteria>) => setCriteria((current) => ({ ...current, ...patch }))
  const runPreview = async () => { setBusy(true); setError(null); setSavedName(null); try { setPreview(await previewSmartCrate(criteria)) } catch (err) { setError(err instanceof ApiError ? err.displayMessage : 'Could not generate a suggestion.') } finally { setBusy(false) } }
  const save = async () => { setBusy(true); setError(null); try { const crate = await saveSmartCrate(criteria); setSavedName(crate.name) } catch (err) { setError(err instanceof ApiError ? err.displayMessage : 'Could not save this crate.') } finally { setBusy(false) } }
  return <main className="crate-workspace manual-crates-page smart-crates-page">
    <PageHeader title="Smart Crates" subtitle="Generate explainable local suggestions, then save the one you want as an editable Manual Crate." />
    <StatusStrip tone="info" icon={<Lightbulb size={15} />} footnote="Suggestions only · no files, tags, BPM, key, cue, or Mixed In Key data are changed.">Smart Crates read existing local library metadata and persist nothing until you save.</StatusStrip>
    {error && <StatusStrip tone="danger" role="alert" onDismiss={() => setError(null)}>{error}</StatusStrip>}
    {savedName && <StatusStrip tone="good" actions={<Link className="btn btn--ghost btn--xs" to="/crates">Open Manual Crates</Link>}>Saved “{savedName}” as a normal editable Manual Crate.</StatusStrip>}
    <div className="manual-crates-kpis"><KpiCard tone="cyan" icon={<WandSparkles size={18} />} label="Preview tracks" value={preview?.track_count ?? '—'} sub="Generated locally" /><KpiCard tone="violet" icon={<Lightbulb size={18} />} label="Unavailable filters" value="2" sub="Energy/vibe and date added" /></div>
    <div className="smart-crates-layout">
      <aside className="smart-crates-controls"><h2>Presets</h2>{loading ? <p className="muted"><Loader2 className="spin" size={14} /> Loading presets…</p> : presets.map((preset) => <button key={preset.id} className="smart-preset" onClick={() => { setCriteria(preset.criteria); setPreview(null); setSavedName(null) }}><strong>{preset.label}</strong><span>{preset.description}</span></button>)}<p className="muted smart-unavailable">Energy/vibe and date-added filters are unavailable because the current tracks data has no trusted fields for them.</p></aside>
      <section className="smart-crates-work"><div className="smart-criteria"><label>Crate name<input className="form-input" value={criteria.name ?? ''} maxLength={120} onChange={(e) => update({ name: e.target.value })} /></label><label>BPM min<input className="form-input" type="number" value={criteria.bpm_min ?? ''} onChange={(e) => update({ bpm_min: numberOrNull(e.target.value) })} /></label><label>BPM max<input className="form-input" type="number" value={criteria.bpm_max ?? ''} onChange={(e) => update({ bpm_max: numberOrNull(e.target.value) })} /></label><label>Camelot key<input className="form-input" placeholder="e.g. 8A" value={criteria.camelot_key ?? ''} onChange={(e) => update({ camelot_key: e.target.value || null })} /></label><label>Harmonic mode<select className="form-input" value={criteria.harmonic_mode ?? ''} onChange={(e) => update({ harmonic_mode: e.target.value ? e.target.value as SmartCrateCriteria['harmonic_mode'] : null })}><option value="">No key filter</option><option value="exact">Exact</option><option value="compatible">Compatible</option><option value="energy_up">Energy up</option><option value="energy_down">Energy down</option></select></label><label>Genres<input className="form-input" placeholder="Afro House, Amapiano" value={criteria.genres.join(', ')} onChange={(e) => update({ genres: e.target.value.split(',').map((v) => v.trim()).filter(Boolean) })} /></label><label>Max tracks<input className="form-input" type="number" min="1" max="100" value={criteria.limit} onChange={(e) => update({ limit: Math.max(1, Math.min(100, Number(e.target.value) || 1)) })} /></label><label className="form-check"><input type="checkbox" checked={criteria.issue_free_only} onChange={(e) => update({ issue_free_only: e.target.checked })} /> Issue-free only</label></div><div className="smart-actions"><button className="btn btn--primary" disabled={busy || !criteria.name?.trim()} onClick={() => void runPreview()}>{busy ? <><Loader2 className="spin" size={14} /> Generating…</> : <><WandSparkles size={14} /> Preview suggestion</>}</button><button className="btn btn--ghost" disabled={busy || !preview?.track_count || !criteria.name?.trim()} onClick={() => void save()}><Save size={14} /> Save as Manual Crate</button></div>
      {preview ? <section className="smart-preview"><div className="manual-crate-section-head"><div><h2>{preview.generated_name}</h2><p>{preview.explanation}</p></div><Badge tone="info">{preview.track_count} tracks</Badge></div>{preview.warnings.map((warning) => <StatusStrip key={warning} tone="warn">{warning}</StatusStrip>)}<AudioPreviewPlayer track={previewTrack} />{preview.tracks.length ? <div className="manual-crate-tracks">{preview.tracks.map((track) => <div className="manual-crate-track" key={track.track_id}><span className="manual-crate-position">{track.position}</span><div className="manual-crate-track-main"><strong>{titleOf(track)}</strong><span>{track.reasons.join(' · ')}</span></div>{track.bpm != null && <Badge tone="pending">{track.bpm.toFixed(1)} BPM</Badge>}{track.key_camelot && <Badge tone="info">{track.key_camelot}</Badge>}{track.genre && <Badge tone="running">{track.genre}</Badge>}<div className="manual-crate-track-actions"><button className="btn btn--ghost btn--xs" onClick={() => setPreviewTrack({ id: track.track_id, artist: track.artist, title: track.title, filename: track.filename, genre: track.genre, bpm: track.bpm, key_camelot: track.key_camelot, duration_sec: track.duration_sec })}>Preview</button></div></div>)}</div> : <EmptyState icon={<WandSparkles size={24} />} title="No matching tracks" message="Broaden the criteria, then generate another local preview." />}</section> : <EmptyState icon={<WandSparkles size={26} />} title="Build a preview" message="Choose a preset or criteria, then preview the suggested ordered track list before saving." />}</section>
    </div>
  </main>
}
