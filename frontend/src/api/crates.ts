import { apiFetch } from './client'
import type { CrateDetail, CrateSummary, CreateCrateInput, UpdateCrateInput } from '../types/crate'

export const fetchCrates = () => apiFetch.get<CrateSummary[]>('/crates')
export const fetchCrate = (id: number) => apiFetch.get<CrateDetail>(`/crates/${id}`)
export const createCrate = (input: CreateCrateInput) => apiFetch.post<CrateSummary>('/crates', input)
export const updateCrate = (id: number, input: UpdateCrateInput) => apiFetch.patch<CrateSummary>(`/crates/${id}`, input)
export const deleteCrate = (id: number) => apiFetch.delete<void>(`/crates/${id}`)
export const addTrackToCrate = (crateId: number, trackId: number) => apiFetch.post<CrateDetail>(`/crates/${crateId}/tracks`, { track_id: trackId })
export const removeTrackFromCrate = (crateId: number, trackId: number) => apiFetch.delete<CrateDetail>(`/crates/${crateId}/tracks/${trackId}`)
export const reorderCrateTracks = (crateId: number, trackIds: number[]) => apiFetch.patch<CrateDetail>(`/crates/${crateId}/tracks/reorder`, { track_ids: trackIds })
