import type {
  TagWriteApplyResultItem,
  TagWritePlan,
  TagWritePlanItem,
} from '../../types/tagWrite'

export const TAG_WRITE_BATCH_LIMIT = 50

export function chunkTrackIds(trackIds: number[], size = TAG_WRITE_BATCH_LIMIT): number[][] {
  if (size < 1) throw new Error('Chunk size must be positive.')
  const chunks: number[][] = []
  for (let index = 0; index < trackIds.length; index += size) {
    chunks.push(trackIds.slice(index, index + size))
  }
  return chunks
}

export function mergeTagWritePlans(plans: TagWritePlan[]): TagWritePlan {
  const items = plans.flatMap((plan) => plan.items)
  return {
    items,
    track_count: plans.reduce((total, plan) => total + plan.track_count, 0),
    changeable_count: plans.reduce((total, plan) => total + plan.changeable_count, 0),
    no_op_count: plans.reduce((total, plan) => total + plan.no_op_count, 0),
    blocked_count: plans.reduce((total, plan) => total + plan.blocked_count, 0),
    additions: plans.reduce((total, plan) => total + plan.additions, 0),
    replacements: plans.reduce((total, plan) => total + plan.replacements, 0),
    clears: plans.reduce((total, plan) => total + plan.clears, 0),
    backup_space_estimate_bytes: plans.reduce((total, plan) => total + plan.backup_space_estimate_bytes, 0),
    writable_fields: plans[0]?.writable_fields ?? [],
    supported_formats: plans[0]?.supported_formats ?? [],
    message: 'Preview only. No file, backup, or local index changes were made.',
  }
}

export type SaveToFileOutcomeStatus = 'saved' | 'already_current' | 'blocked' | 'failed'

export interface SaveToFileOutcome {
  track_id: number
  filename: string | null
  relative_path: string | null
  status: SaveToFileOutcomeStatus
  fields: TagWritePlanItem['fields']
  reason?: string
}

export interface SaveToFileSummary {
  saved: number
  already_current: number
  blocked: number
  failed: number
  outcomes: SaveToFileOutcome[]
}

/**
 * Reconciles the plan with the writer's verified per-track results. An HTTP
 * success is not enough: only an applied result with verified=true is saved.
 */
export function summarizeTagWriteResults(
  plan: TagWritePlan,
  results: TagWriteApplyResultItem[],
  requestErrors: Record<number, string> = {},
): SaveToFileSummary {
  const resultById = new Map(results.map((result) => [result.track_id, result]))
  const outcomes = plan.items.map((item): SaveToFileOutcome => {
    if (item.blocked) {
      return {
        track_id: item.track_id,
        filename: item.filename,
        relative_path: item.relative_path,
        status: 'blocked',
        fields: item.fields,
        reason: item.blocker ?? 'This track cannot be written.',
      }
    }
    if (!item.fields.length) {
      return {
        track_id: item.track_id,
        filename: item.filename,
        relative_path: item.relative_path,
        status: 'already_current',
        fields: item.fields,
        reason: 'No approved fields differ from the file.',
      }
    }

    const result = resultById.get(item.track_id)
    if (result?.status === 'applied' && result.verified === true) {
      return {
        track_id: item.track_id,
        filename: item.filename,
        relative_path: item.relative_path,
        status: 'saved',
        fields: item.fields,
      }
    }
    if (result?.status === 'skipped' && !result.reason?.toLowerCase().includes('stale')) {
      return {
        track_id: item.track_id,
        filename: item.filename,
        relative_path: item.relative_path,
        status: 'already_current',
        fields: item.fields,
        reason: result.reason ?? 'No approved fields differ from the file.',
      }
    }
    return {
      track_id: item.track_id,
      filename: item.filename,
      relative_path: item.relative_path,
      status: 'failed',
      fields: item.fields,
      reason: requestErrors[item.track_id] ?? result?.reason ?? 'The file write did not verify.',
    }
  })
  return {
    saved: outcomes.filter((outcome) => outcome.status === 'saved').length,
    already_current: outcomes.filter((outcome) => outcome.status === 'already_current').length,
    blocked: outcomes.filter((outcome) => outcome.status === 'blocked').length,
    failed: outcomes.filter((outcome) => outcome.status === 'failed').length,
    outcomes,
  }
}
