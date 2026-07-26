import type { CSSProperties } from 'react'
import type { CamelotMatchType, QualityTier } from '../../types/track'

export type SortKey = 'artist' | 'title' | 'bpm' | 'filename'
export type SortOrder = 'asc' | 'desc'
export type Density = 'comfortable' | 'compact'

export const LIMIT = 50
export const TRACK_OVERSCAN = 6
export const UI_STATE_KEY = 'crateiq.library.ui.v1'

/**
 * Row height per density mode — used both for CSS (via inline style) and to
 * compute the virtualization window, so the two never drift out of sync.
 */
export const ROW_HEIGHT: Record<Density, number> = { comfortable: 44, compact: 34 }
export const TABLE_VIEWPORT_HEIGHT = 460

export interface LibraryUiState {
  searchDraft: string
  search: string
  offset: number
  sort: SortKey
  order: SortOrder
  selectedId: number | null
  genreFilter: string
  bpmMinFilter: string
  bpmMaxFilter: string
  hasKeyFilter: '' | 'yes' | 'no'
  density: Density
  filtersExpanded: boolean
  statusStripCollapsed: boolean
}

export const DEFAULT_UI_STATE: LibraryUiState = {
  searchDraft: '',
  search: '',
  offset: 0,
  sort: 'artist',
  order: 'asc',
  selectedId: null,
  genreFilter: '',
  bpmMinFilter: '',
  bpmMaxFilter: '',
  hasKeyFilter: '',
  density: 'comfortable',
  filtersExpanded: true,
  statusStripCollapsed: false,
}

function safeString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function safeNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : fallback
}

function isSortKey(value: unknown): value is SortKey {
  return value === 'artist' || value === 'title' || value === 'bpm' || value === 'filename'
}

export function loadUiState(): LibraryUiState {
  try {
    const raw = window.localStorage.getItem(UI_STATE_KEY)
    if (!raw) return { ...DEFAULT_UI_STATE }
    const input = JSON.parse(raw) as Record<string, unknown>
    const searchDraft = safeString(input.searchDraft ?? input.search, '')
    return {
      searchDraft,
      search: safeString(input.search, searchDraft),
      offset: safeNumber(input.offset, 0),
      sort: isSortKey(input.sort) ? input.sort : DEFAULT_UI_STATE.sort,
      order: input.order === 'desc' ? 'desc' : 'asc',
      selectedId: typeof input.selectedId === 'number' && Number.isInteger(input.selectedId) && input.selectedId > 0
        ? input.selectedId
        : null,
      genreFilter: safeString(input.genreFilter, ''),
      bpmMinFilter: safeString(input.bpmMinFilter, ''),
      bpmMaxFilter: safeString(input.bpmMaxFilter, ''),
      hasKeyFilter: input.hasKeyFilter === 'yes' || input.hasKeyFilter === 'no' ? input.hasKeyFilter : '',
      density: input.density === 'compact' ? 'compact' : 'comfortable',
      filtersExpanded: typeof input.filtersExpanded === 'boolean' ? input.filtersExpanded : true,
      statusStripCollapsed: typeof input.statusStripCollapsed === 'boolean' ? input.statusStripCollapsed : false,
    }
  } catch {
    return { ...DEFAULT_UI_STATE }
  }
}

export function persistUiState(state: LibraryUiState) {
  try {
    window.localStorage.setItem(UI_STATE_KEY, JSON.stringify(state))
  } catch {
    // localStorage may be unavailable in restricted browser contexts.
  }
}

export function pct(value: number, total: number): number {
  if (!total) return 0
  return Math.max(0, Math.min(100, (value / total) * 100))
}

export function displayValue(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined) return fallback
  if (typeof value === 'string') {
    const trimmed = value.trim()
    return trimmed ? trimmed : fallback
  }
  if (typeof value === 'number' && Number.isNaN(value)) return fallback
  return String(value)
}

/**
 * Maps a Camelot code (e.g. "9A") to a hue around the standard 12-step
 * Camelot color wheel, purely as a real, data-driven visual grouping — not a
 * fabricated value. Returns null when the track has no Camelot key.
 */
export function camelotHue(camelot: string | null | undefined): number | null {
  if (!camelot) return null
  const match = /^(\d{1,2})[ab]$/i.exec(camelot.trim())
  if (!match) return null
  const n = parseInt(match[1], 10)
  if (n < 1 || n > 12) return null
  return ((n - 1) * 30) % 360
}

export function camelotTextColor(camelot: string | null | undefined): CSSProperties | undefined {
  const hue = camelotHue(camelot)
  if (hue === null) return undefined
  const minor = /a$/i.test((camelot ?? '').trim())
  return { color: `hsl(${hue} 80% ${minor ? 72 : 80}%)` }
}

export function camelotStyle(camelot: string | null | undefined): CSSProperties | undefined {
  const hue = camelotHue(camelot)
  if (hue === null) return undefined
  const minor = /a$/i.test((camelot ?? '').trim())
  return {
    color: `hsl(${hue} 80% ${minor ? 72 : 80}%)`,
    background: `hsla(${hue}, 65%, 50%, 0.16)`,
    borderColor: `hsla(${hue}, 65%, 55%, 0.35)`,
  }
}

export function camelotHeroStyle(camelot: string | null | undefined): CSSProperties | undefined {
  const hue = camelotHue(camelot)
  if (hue === null) return undefined
  return {
    background: `radial-gradient(circle at 35% 30%, hsla(${hue}, 85%, 68%, 0.55), hsla(${hue}, 70%, 30%, 0.55) 70%)`,
    borderColor: `hsla(${hue}, 75%, 62%, 0.55)`,
    boxShadow: `0 0 0 1px hsla(${hue}, 75%, 60%, 0.2), 0 4px 18px hsla(${hue}, 70%, 45%, 0.35)`,
  }
}

export type QualityTierValue = QualityTier | null | undefined

/** Ranks quality_tier onto a 1-4 scale purely for the compact meter visual. */
export function qualityRank(tier: QualityTierValue): number {
  switch (tier) {
    case 'LOSSLESS': return 4
    case 'HIGH': return 3
    case 'MEDIUM': return 2
    case 'LOW': return 1
    default: return 0
  }
}

// ---------------------------------------------------------------------------
// Camelot wheel math — shared by the table/inspector color coding above and
// CamelotWheel.tsx's SVG rendering.
// ---------------------------------------------------------------------------

/** Alias kept for parity with CamelotWheel's helper naming; same math as camelotHue(). */
export const getCamelotHue = camelotHue

export interface ParsedCamelotKey {
  number: number
  letter: 'A' | 'B'
  code: string
}

/** Parse a Camelot code like "8a" into { number: 8, letter: 'A', code: '8A' }, or null. */
export function parseCamelotKey(camelot: string | null | undefined): ParsedCamelotKey | null {
  if (!camelot) return null
  const match = /^(1[0-2]|[1-9])([ABab])$/.exec(camelot.trim())
  if (!match) return null
  const number = parseInt(match[1], 10)
  const letter = match[2].toUpperCase() as 'A' | 'B'
  return { number, letter, code: `${number}${letter}` }
}

export interface CompatibleCamelotKeys {
  sameKey: string
  adjacent: [string, string]
  relative: string
}

/**
 * Return the three standard mixable relations for a Camelot key: itself,
 * its two wheel-adjacent neighbors (same letter), and its relative
 * major/minor (same number, other letter). Mirrors the inclusion rules used
 * by GET /api/tracks/{id}/compatible (modules/harmonic.py's wheel distance).
 */
export function getCompatibleCamelotKeys(camelot: string | null | undefined): CompatibleCamelotKeys | null {
  const parsed = parseCamelotKey(camelot)
  if (!parsed) return null
  const { number, letter } = parsed
  const prev = ((number + 10) % 12) + 1
  const next = (number % 12) + 1
  const other: 'A' | 'B' = letter === 'A' ? 'B' : 'A'
  return {
    sameKey: `${number}${letter}`,
    adjacent: [`${prev}${letter}`, `${next}${letter}`],
    relative: `${number}${other}`,
  }
}

const MATCH_TYPE_LABELS: Record<CamelotMatchType, string> = {
  same_key: 'Same key',
  adjacent_key: 'Adjacent key',
  relative_key: 'Relative major/minor',
}

/** Human-readable label for a compatible-tracks match_type; falls back for unknown values. */
export function formatCompatibilityReason(matchType: CamelotMatchType | string | null | undefined): string {
  if (matchType && matchType in MATCH_TYPE_LABELS) return MATCH_TYPE_LABELS[matchType as CamelotMatchType]
  return 'Compatible'
}
