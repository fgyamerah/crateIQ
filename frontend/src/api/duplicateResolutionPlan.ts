import { apiFetch } from './client'
import type { DuplicateResolutionPlanResponse } from '../types/duplicateResolutionPlan'

export const fetchDuplicateResolutionPlan = () => apiFetch.get<DuplicateResolutionPlanResponse>('/duplicates/resolution-plan')
