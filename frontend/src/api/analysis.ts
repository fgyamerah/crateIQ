import { apiFetch } from './client'
import type {
  BpmAnomaly,
  BpmCheckResult,
  BpmSummary,
  MikCoverageResult,
  MikImportResult,
  AnalysisJobHistory,
  AnalysisJobPreview,
  AnalysisJobType,
  AnalysisJobDefinition,
  BpmAnalysisRunResult,
  ReanalyzeRequest,
  UpdateAnomalyRequest,
} from '../types/analysis'
import type { Job } from '../types/job'

export function runBpmCheck(): Promise<BpmCheckResult> {
  return apiFetch.post<BpmCheckResult>('/analysis/bpm-check', {})
}

export function fetchBpmAnomalies(params: {
  status?: string
  reason?: string
  limit?: number
  offset?: number
} = {}): Promise<BpmAnomaly[]> {
  const parts: string[] = []
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    }
  }
  const qs = parts.length ? `?${parts.join('&')}` : ''
  return apiFetch.get<BpmAnomaly[]>(`/analysis/bpm-anomalies${qs}`)
}

export function fetchBpmSummary(): Promise<BpmSummary> {
  return apiFetch.get<BpmSummary>('/analysis/bpm-anomalies/summary')
}

export function updateAnomaly(
  id: number,
  body: UpdateAnomalyRequest,
): Promise<BpmAnomaly> {
  return apiFetch.patch<BpmAnomaly>(`/analysis/bpm-anomalies/${id}`, body)
}

export function submitReanalyze(body: ReanalyzeRequest): Promise<Job> {
  return apiFetch.post<Job>('/analysis/reanalyze', body)
}

export const fetchMikCoverage = () => apiFetch.get<MikCoverageResult>('/analysis/mik/coverage')
export const previewMikMetadata = () => apiFetch.post<MikCoverageResult>('/analysis/mik/preview', {})
export const importMikMetadata = () => apiFetch.post<MikImportResult>('/analysis/mik/import', {})
export const fetchAnalysisJobs = () => apiFetch.get<{ jobs: AnalysisJobDefinition[] }>('/analysis/jobs')
export const previewAnalysisJob = (jobType: AnalysisJobType) => apiFetch.get<AnalysisJobPreview>(`/analysis/jobs/${jobType}/preview`)
export const fetchAnalysisJobHistory = () => apiFetch.get<AnalysisJobHistory>('/analysis/jobs/history')
export const runBpmAnalysis = (limit: number) => apiFetch.post<BpmAnalysisRunResult>('/analysis/jobs/bpm_analysis/run', { confirm: true, limit })
