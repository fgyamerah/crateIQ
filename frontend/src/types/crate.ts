export interface CrateSummary {
  id: number
  name: string
  notes: string | null
  created_at: string
  updated_at: string
  track_count: number
}

export interface CrateTrack {
  track_id: number
  position: number
  added_at: string
  note: string | null
  artist: string | null
  title: string | null
  filename: string | null
  genre: string | null
  bpm: number | null
  key_camelot: string | null
  duration_sec: number | null
  missing_from_library: boolean
}

export interface CrateDetail extends CrateSummary {
  tracks: CrateTrack[]
}

export interface CreateCrateInput { name: string; notes?: string | null }
export interface UpdateCrateInput { name?: string; notes?: string | null }
