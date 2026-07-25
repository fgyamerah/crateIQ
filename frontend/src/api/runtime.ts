import { apiFetch } from './client'

export type ReadinessStatus = 'ready' | 'degraded' | 'not_ready'
export type CheckStatus = 'pass' | 'warn' | 'fail'

export interface ReadinessCheck {
  name: string
  status: CheckStatus
  message: string
  required: boolean
  metadata?: Record<string, unknown>
}

export interface ReadinessResponse {
  status: ReadinessStatus
  checks: ReadinessCheck[]
}

export const fetchReadiness = (): Promise<ReadinessResponse> =>
  apiFetch.get<ReadinessResponse>('/runtime/readiness')
