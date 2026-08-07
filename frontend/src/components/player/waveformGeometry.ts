/**
 * Pure geometry and state helpers for real waveform rendering (W4).
 *
 * Everything here is a deterministic function of its inputs — no DOM, no
 * fetch, no React. Keeping this separate makes the maths reviewable and
 * testable independently of canvas rendering.
 */
import type { WaveformArtifactStatus } from '../../api/waveforms'

/** Signed 16-bit full scale, matching the backend's documented encoding. */
export const DEFAULT_PEAK_SCALE = 32767

/** One rendered column: the signed extrema of every peak pair it covers. */
export interface WaveformBar {
  /** Minimum sample in this column, normalized to -1..1. */
  min: number
  /** Maximum sample in this column, normalized to -1..1. */
  max: number
}

function clampUnit(value: number): number {
  if (!Number.isFinite(value)) return 0
  if (value < -1) return -1
  if (value > 1) return 1
  return value
}

function clampUnit01(value: number): number {
  if (!Number.isFinite(value)) return 0
  if (value < 0) return 0
  if (value > 1) return 1
  return value
}

/**
 * Reduce interleaved `[min0, max0, min1, max1, ...]` peaks to at most
 * `barCount` columns, preserving extrema exactly as the backend does: each
 * column takes the minimum of contributing minima and the maximum of
 * contributing maxima. Averaging would erase transients.
 *
 * Never fabricates columns: a short peak array yields one column per pair
 * rather than being stretched to `barCount`.
 */
export function buildWaveformBars(
  peaks: readonly number[],
  barCount: number,
  scale: number = DEFAULT_PEAK_SCALE,
): WaveformBar[] {
  const pairCount = Math.floor(peaks.length / 2)
  const safeScale = Number.isFinite(scale) && scale > 0 ? scale : DEFAULT_PEAK_SCALE
  if (pairCount <= 0 || !Number.isFinite(barCount) || barCount <= 0) return []

  const columns = Math.min(Math.floor(barCount), pairCount)
  const bars: WaveformBar[] = new Array(columns)

  for (let index = 0; index < columns; index += 1) {
    const start = Math.floor((index * pairCount) / columns)
    let end = Math.floor(((index + 1) * pairCount) / columns)
    if (end <= start) end = start + 1
    if (end > pairCount) end = pairCount

    let lo = peaks[start * 2]
    let hi = peaks[start * 2 + 1]
    for (let pair = start + 1; pair < end; pair += 1) {
      const candidateMin = peaks[pair * 2]
      const candidateMax = peaks[pair * 2 + 1]
      if (candidateMin < lo) lo = candidateMin
      if (candidateMax > hi) hi = candidateMax
    }

    bars[index] = {
      min: clampUnit(lo / safeScale),
      max: clampUnit(hi / safeScale),
    }
  }
  return bars
}

// ---------------------------------------------------------------------------
// Amplitude coloring (Night Deck)
// ---------------------------------------------------------------------------

/**
 * Amplitude → hue stops for the Night Deck waveform style. Purely a function
 * of each bar's already-normalized peak magnitude (see `buildWaveformBars`) —
 * no frequency/spectral data is involved.
 */
const AMPLITUDE_COLOR_STOPS: ReadonlyArray<{
  readonly amplitude: number
  readonly rgb: readonly [number, number, number]
}> = [
  { amplitude: 0.0, rgb: [0x23, 0x88, 0xff] }, // quiet blue
  { amplitude: 0.2, rgb: [0x00, 0xc8, 0xe8] }, // cyan
  { amplitude: 0.4, rgb: [0x28, 0xd9, 0x6b] }, // green
  { amplitude: 0.6, rgb: [0xf5, 0xd9, 0x0a] }, // yellow
  { amplitude: 0.8, rgb: [0xff, 0x8a, 0x00] }, // orange
  { amplitude: 1.0, rgb: [0xff, 0x3b, 0x30] }, // peak red
]

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

/**
 * Gamma applied to amplitude before color lookup only (never to bar height).
 * Mastered dance material sits with most bars at high raw amplitude, which
 * would otherwise saturate the ramp into orange/red almost everywhere; this
 * pulls typical (~0.3-0.8) amplitudes down so the blue/cyan/green/yellow
 * range stays visible and red reads as "true peak" rather than "normal".
 */
const COLOR_GAMMA = 2.2

/**
 * Maps a bar's normalized amplitude (0..1) to a Night Deck waveform color,
 * continuously interpolated across the blue → cyan → green → yellow →
 * orange → red ramp (never a five-color stripe).
 *
 * `intensity` (0..1) scales alpha so played/upcoming sections can be
 * distinguished by brightness while keeping the same hue.
 */
export function waveformAmplitudeColor(normalizedAmplitude: number, intensity = 1): string {
  const amplitude = clampUnit01(normalizedAmplitude) ** COLOR_GAMMA
  const alpha = clampUnit01(intensity)

  let lower = AMPLITUDE_COLOR_STOPS[0]
  let upper = AMPLITUDE_COLOR_STOPS[AMPLITUDE_COLOR_STOPS.length - 1]
  for (let index = 0; index < AMPLITUDE_COLOR_STOPS.length - 1; index += 1) {
    const candidateLower = AMPLITUDE_COLOR_STOPS[index]
    const candidateUpper = AMPLITUDE_COLOR_STOPS[index + 1]
    if (amplitude >= candidateLower.amplitude && amplitude <= candidateUpper.amplitude) {
      lower = candidateLower
      upper = candidateUpper
      break
    }
  }

  const span = upper.amplitude - lower.amplitude
  const t = span > 0 ? (amplitude - lower.amplitude) / span : 0
  const r = Math.round(lerp(lower.rgb[0], upper.rgb[0], t))
  const g = Math.round(lerp(lower.rgb[1], upper.rgb[1], t))
  const b = Math.round(lerp(lower.rgb[2], upper.rgb[2], t))
  return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`
}

/**
 * Playback progress as a 0..1 fraction.
 *
 * Returns 0 for unknown, zero, negative, NaN, or infinite duration so the
 * overlay degrades to "nothing played" rather than rendering nonsense.
 */
export function progressFraction(currentTime: number, duration: number): number {
  if (!Number.isFinite(currentTime) || !Number.isFinite(duration)) return 0
  if (duration <= 0 || currentTime <= 0) return 0
  const fraction = currentTime / duration
  if (!Number.isFinite(fraction)) return 0
  return fraction >= 1 ? 1 : fraction
}

// ---------------------------------------------------------------------------
// Lifecycle presentation
// ---------------------------------------------------------------------------

/** How the player should present one waveform lifecycle state. */
export interface WaveformPresentation {
  /** Short, user-facing status line. Empty string means "say nothing". */
  message: string
  /** Show the explicit "Generate waveform" action. */
  canGenerate: boolean
  /** Show the cancel-generation action. */
  canCancel: boolean
  /** Generation is actively queued or running. */
  busy: boolean
  /** This is a degraded state the user should notice, not a neutral one. */
  degraded: boolean
}

/**
 * Map a lifecycle state to player UI.
 *
 * Deliberately never collapses distinct states into a generic error: queued,
 * processing, failed, unsupported, stale, and cancelled all read differently.
 * Copy avoids internal detail (no FFmpeg, cache, or path references).
 */
export function presentWaveformState(
  status: WaveformArtifactStatus | 'loading',
  generationAvailable = true,
): WaveformPresentation {
  switch (status) {
    case 'loading':
      return { message: 'Checking waveform…', canGenerate: false, canCancel: false, busy: false, degraded: false }
    case 'ready':
      return { message: '', canGenerate: false, canCancel: false, busy: false, degraded: false }
    case 'queued':
      return { message: 'Waveform queued', canGenerate: false, canCancel: true, busy: true, degraded: false }
    case 'processing':
      return { message: 'Generating waveform…', canGenerate: false, canCancel: true, busy: true, degraded: false }
    case 'not_generated':
      return {
        message: 'No waveform yet',
        canGenerate: generationAvailable,
        canCancel: false,
        busy: false,
        degraded: false,
      }
    case 'stale':
      return {
        message: 'Waveform out of date',
        canGenerate: generationAvailable,
        canCancel: false,
        busy: false,
        degraded: false,
      }
    case 'cancelled':
      return {
        message: 'Waveform generation cancelled',
        canGenerate: generationAvailable,
        canCancel: false,
        busy: false,
        degraded: false,
      }
    case 'failed':
      return {
        message: "Couldn't generate waveform",
        canGenerate: generationAvailable,
        canCancel: false,
        busy: false,
        degraded: true,
      }
    case 'unsupported':
      return {
        message: 'Waveform unavailable for this format',
        canGenerate: false,
        canCancel: false,
        busy: false,
        degraded: true,
      }
    default:
      return { message: '', canGenerate: false, canCancel: false, busy: false, degraded: false }
  }
}
