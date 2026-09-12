export interface UserPlaylistSummary {
  id: number
  name: string
  description: string | null
  created_at: string
  updated_at: string
  track_count: number
}

export interface UserPlaylistTrack {
  track_id: number
  position: number
  added_at: string
  artist: string | null
  title: string | null
  filename: string | null
  filepath: string | null
  genre: string | null
  comment: string | null
  label: string | null
  bpm: number | null
  key_musical: string | null
  key_camelot: string | null
  duration_sec: number | null
  bitrate_kbps: number | null
  status: string | null
  quality_tier: string | null
  parse_confidence: string | null
  storage_zone: string | null
  rating: number | null
  favorite: boolean
  missing_from_library: boolean
}

export interface UserPlaylistDetail extends UserPlaylistSummary {
  tracks: UserPlaylistTrack[]
}

export interface PlaylistAddPreview {
  playlist_id: number
  selected_count: number
  will_add_count: number
  already_present_count: number
  missing_count: number
  track_ids: number[]
  message: string
}

export interface PlaylistMutationResponse {
  playlist: UserPlaylistDetail
  selected_count: number
  added_count: number
  already_present_count: number
  missing_count: number
  message: string
}

export interface CreateUserPlaylistInput {
  name: string
  description?: string | null
}

export interface UpdateUserPlaylistInput {
  name?: string
  description?: string | null
}

export type UserPlaylistSort = 'manual' | 'artist' | 'title' | 'rating' | 'favorite'
