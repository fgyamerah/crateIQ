import { apiFetch } from './client'
import type {
  CreateUserPlaylistInput,
  PlaylistAddPreview,
  PlaylistMutationResponse,
  UpdateUserPlaylistInput,
  UserPlaylistDetail,
  UserPlaylistSort,
  UserPlaylistSummary,
} from '../types/userPlaylist'

export function fetchUserPlaylists(): Promise<UserPlaylistSummary[]> {
  return apiFetch.get<UserPlaylistSummary[]>('/user-playlists')
}

export function fetchUserPlaylist(
  id: number,
  params: { search?: string; sort?: UserPlaylistSort; order?: 'asc' | 'desc'; favoriteOnly?: boolean } = {},
): Promise<UserPlaylistDetail> {
  const query = new URLSearchParams()
  if (params.search) query.set('search', params.search)
  if (params.sort) query.set('sort', params.sort)
  if (params.order) query.set('order', params.order)
  if (params.favoriteOnly) query.set('favorite_only', 'true')
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return apiFetch.get<UserPlaylistDetail>(`/user-playlists/${id}${suffix}`)
}

export function createUserPlaylist(input: CreateUserPlaylistInput): Promise<UserPlaylistSummary> {
  return apiFetch.post<UserPlaylistSummary>('/user-playlists', input)
}

export function updateUserPlaylist(id: number, input: UpdateUserPlaylistInput): Promise<UserPlaylistSummary> {
  return apiFetch.patch<UserPlaylistSummary>(`/user-playlists/${id}`, input)
}

export function deleteUserPlaylist(id: number): Promise<void> {
  return apiFetch.delete<void>(`/user-playlists/${id}`)
}

export function previewAddToPlaylist(id: number, trackIds: number[]): Promise<PlaylistAddPreview> {
  return apiFetch.post<PlaylistAddPreview>(`/user-playlists/${id}/tracks/preview`, { track_ids: trackIds })
}

export function addToPlaylist(id: number, trackIds: number[], confirm = false): Promise<PlaylistMutationResponse> {
  return apiFetch.post<PlaylistMutationResponse>(`/user-playlists/${id}/tracks`, { track_ids: trackIds, confirm })
}

export function removeFromPlaylist(id: number, trackId: number): Promise<UserPlaylistDetail> {
  return apiFetch.delete<UserPlaylistDetail>(`/user-playlists/${id}/tracks/${trackId}`)
}

export function removeSelectedFromPlaylist(id: number, trackIds: number[]): Promise<UserPlaylistDetail> {
  return apiFetch.post<UserPlaylistDetail>(`/user-playlists/${id}/tracks/remove`, { track_ids: trackIds })
}

export function reorderPlaylist(id: number, trackIds: number[]): Promise<UserPlaylistDetail> {
  return apiFetch.patch<UserPlaylistDetail>(`/user-playlists/${id}/tracks/reorder`, { track_ids: trackIds })
}
