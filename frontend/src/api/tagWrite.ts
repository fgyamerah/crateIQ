import { apiFetch } from './client'
import type { TagWriteApplyResult, TagWriteOperation, TagWritePlan, TagWritePlanItem } from '../types/tagWrite'

export const fetchTagWritePlan = (trackIds: number[]) =>
  apiFetch.post<TagWritePlan>('/tag-write/plan', { track_ids: trackIds })

export const applyTagWritePlan = (items: TagWritePlanItem[]) => {
  const trackIds = items.map((item) => item.track_id)
  const expected: Record<number, { expected_size: number; expected_mtime_ns: string }> = {}
  for (const item of items) {
    if (!item.blocked && item.expected_size !== undefined && item.expected_mtime_ns !== undefined) {
      expected[item.track_id] = { expected_size: item.expected_size, expected_mtime_ns: item.expected_mtime_ns }
    }
  }
  return apiFetch.post<TagWriteApplyResult>('/tag-write/apply', { track_ids: trackIds, expected, confirm: true })
}

export const fetchTagWriteOperations = (limit = 20) =>
  apiFetch.get<TagWriteOperation[]>(`/tag-write/operations?limit=${limit}`)

export const fetchTagWriteOperation = (operationId: string) =>
  apiFetch.get<TagWriteOperation>(`/tag-write/operations/${operationId}`)

export const restoreTagWriteFile = (operationId: string, trackId: number) =>
  apiFetch.post<{ track_id: number; restored: boolean; verified: boolean; relative_path: string }>(
    `/tag-write/operations/${operationId}/restore/${trackId}`,
    { track_id: trackId, confirm: true },
  )
