export type HarmonicMode = 'exact' | 'compatible' | 'energy_up' | 'energy_down'
export interface SmartCrateCriteria { name?: string | null; notes?: string | null; bpm_min?: number | null; bpm_max?: number | null; camelot_key?: string | null; harmonic_mode?: HarmonicMode | null; genres: string[]; issue_free_only: boolean; limit: number }
export interface SmartCrateTrack { track_id: number; position: number; artist: string | null; title: string | null; filename: string | null; genre: string | null; bpm: number | null; key_camelot: string | null; duration_sec: number | null; reasons: string[] }
export interface SmartCratePreview { criteria: SmartCrateCriteria; generated_name: string; explanation: string; warnings: string[]; track_count: number; tracks: SmartCrateTrack[] }
export interface SmartCratePreset { id: string; label: string; description: string; criteria: SmartCrateCriteria }
