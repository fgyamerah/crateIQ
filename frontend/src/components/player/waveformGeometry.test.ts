import { describe, expect, it } from 'vitest'
import {
  buildWaveformBars,
  downsampleColorBands,
  progressFraction,
  waveformFrequencyColor,
} from './waveformGeometry'

describe('buildWaveformBars', () => {
  it('reduces interleaved peaks to the requested bar count preserving extrema', () => {
    const peaks = [0, 100, -100, 50, -10, 200, 0, 0]
    const bars = buildWaveformBars(peaks, 2, 32767)
    expect(bars).toHaveLength(2)
    // First bar covers the first two pairs; min is -100.
    expect(bars[0].min).toBeLessThan(0)
    // The full-scale maximum must survive into some bar.
    expect(Math.max(...bars.map((b) => b.max))).toBeGreaterThan(0)
  })

  it('never fabricates bars from an empty peak array', () => {
    expect(buildWaveformBars([], 10)).toEqual([])
  })
})

describe('downsampleColorBands', () => {
  it('averages interleaved band triplets to the requested count', () => {
    const colorBands = [1, 0, 0, 0, 1, 0] // two buckets
    const out = downsampleColorBands(colorBands, 1)
    expect(out).toEqual([[0.5, 0.5, 0]])
  })

  it('returns empty for empty input', () => {
    expect(downsampleColorBands([], 4)).toEqual([])
  })
})

describe('waveformFrequencyColor', () => {
  it('returns an rgba color and differs between bass and transient input', () => {
    const bass = waveformFrequencyColor(1, 0, 0)
    const transient = waveformFrequencyColor(0, 0, 1)
    expect(bass).toMatch(/^rgba\(/)
    expect(transient).toMatch(/^rgba\(/)
    expect(bass).not.toBe(transient)
  })
})

describe('progressFraction', () => {
  it('clamps to 0..1 and returns 0 for unknown duration', () => {
    expect(progressFraction(2, 4)).toBe(0.5)
    expect(progressFraction(10, 4)).toBe(1)
    expect(progressFraction(-1, 4)).toBe(0)
    expect(progressFraction(2, 0)).toBe(0)
    expect(progressFraction(2, NaN)).toBe(0)
  })
})
