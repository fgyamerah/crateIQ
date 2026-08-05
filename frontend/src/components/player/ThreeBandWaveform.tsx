import type { CSSProperties } from 'react'

type BandName = 'low' | 'mid' | 'high'

interface Props {
  seed?: number
  inactive?: boolean
  compact?: boolean
  className?: string
}

const BAND_OFFSETS: Record<BandName, number> = {
  low: 17,
  mid: 43,
  high: 79,
}

function deterministicSegments(seed: number, band: BandName, count: number): number[] {
  let value = Math.abs(seed + BAND_OFFSETS[band]) || BAND_OFFSETS[band]
  return Array.from({ length: count }, (_, index) => {
    value = (value * 1664525 + 1013904223 + index * BAND_OFFSETS[band]) >>> 0
    const normalized = (value % 1000) / 1000
    const bandScale = band === 'low' ? 0.72 : band === 'mid' ? 0.88 : 1
    return 0.18 + normalized * bandScale * 0.82
  })
}

/**
 * Decorative low/mid/high equalizer-style waveform placeholder.
 * Heights are deterministic from a track id; no audio is read or analyzed.
 */
export default function ThreeBandWaveform({
  seed = 0,
  inactive = false,
  compact = false,
  className = '',
}: Props) {
  const count = compact ? 22 : 34
  const classes = [
    'three-band-waveform',
    inactive ? 'three-band-waveform--inactive' : '',
    compact ? 'three-band-waveform--compact' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <div
      className={classes}
      data-visual-only="true"
      role="img"
      aria-label="Decorative three-band equalizer preview; no waveform analysis is performed"
    >
      {(['low', 'mid', 'high'] as BandName[]).map((band) => (
        <div className={`three-band-waveform-band three-band-waveform-band--${band}`} key={band}>
          <span className="three-band-waveform-label">{band}</span>
          <span className="three-band-waveform-segments" aria-hidden="true">
            {deterministicSegments(seed, band, count).map((height, index) => (
              <span
                className="three-band-waveform-segment"
                key={`${band}-${index}`}
                style={{ '--wave-height': `${Math.round(height * 100)}%` } as CSSProperties}
              />
            ))}
          </span>
        </div>
      ))}
    </div>
  )
}
