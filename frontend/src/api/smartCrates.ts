import { apiFetch } from './client'
import type { CrateDetail } from '../types/crate'
import type { SmartCrateCriteria, SmartCratePreset, SmartCratePreview } from '../types/smartCrate'
export const fetchSmartCratePresets = () => apiFetch.get<SmartCratePreset[]>('/smart-crates/presets')
export const previewSmartCrate = (criteria: SmartCrateCriteria) => apiFetch.post<SmartCratePreview>('/smart-crates/preview', criteria)
export const saveSmartCrate = (criteria: SmartCrateCriteria) => apiFetch.post<CrateDetail>('/smart-crates/save', criteria)
