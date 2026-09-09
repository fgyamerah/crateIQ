import { useEffect, useMemo, useRef, useState } from 'react'
import {
  buildWaveformBars,
  downsampleColorBands,
  progressFraction,
  waveformAmplitudeColor,
  waveformFrequencyColor,
} from './waveformGeometry'
import type { WaveformArtifactStatus } from '../../api/waveforms'

/**
 * Canonical CrateIQ waveform renderer — the single mirrored, frequency-tinted
 * waveform used everywhere (bottom player, track inspector, Music Review,
 * inbox inspector, analysis panes).
 *
 * ONE mirrored waveform: vertical bars extend above and below one center axis,
 * densely packed, with per-slice color derived from low/mid/high energy
 * fractions when present (falling back to an amplitude ramp). There are never
 * three separate Low/Mid/High rows.
 *
 * Pure presentation: it receives peaks and playback position and draws them.
 * It never fetches, generates, owns job state, or touches audio. Seeking stays
 * with the player's native range control, which the parent overlays.
 */

/** Column pitch in CSS pixels, kept tight so peaks read as a dense envelope. */
const BAR_STRIDE_PX = 2
const BAR_WIDTH_PX = 1.5
/** Keep silence visible as a thin center line rather than nothing. */
const MIN_BAR_PX = 1

/** Played vs. upcoming intensity; hue stays the same, only alpha differs. */
const INTENSITY_PLAYED = 1
const INTENSITY_UPCOMING = 0.72
const COLOR_CENTER_LINE = 'rgba(133, 158, 184, 0.22)'
const COLOR_PLAYHEAD = 'rgba(32, 212, 216, 0.95)'

export type UnifiedWaveformVariant = 'compact' | 'standard' | 'expanded'

/** How many skeleton bars to render per variant. Bounded, never unbounded. */
const SKELETON_BARS: Record<UnifiedWaveformVariant, number> = {
  compact: 40,
  standard: 64,
  expanded: 96,
}

interface Props {
  peaks?: readonly number[]
  /** Interleaved [low, mid, high] energy fractions; null means no tint. */
  colorBands?: readonly number[] | null
  scale?: number
  /** Seconds elapsed, from the persistent player. */
  currentTime?: number
  /** Seconds total, from the persistent player. */
  duration?: number
  /** Dims the waveform when playback is not active. */
  inactive?: boolean
  variant?: UnifiedWaveformVariant
  /**
   * Lifecycle state. `ready` draws the real waveform; `loading`, `queued`, and
   * `processing` draw the animated skeleton; everything else draws the muted
   * empty center line.
   */
  status?: WaveformArtifactStatus | 'loading' | 'idle'
  className?: string
}

const BUSY_STATUSES: ReadonlySet<WaveformArtifactStatus | 'loading'> = new Set([
  'loading',
  'queued',
  'processing',
])

function skeletonHeights(seed: number, count: number): number[] {
  let value = Math.abs(seed) || 1
  return Array.from({ length: count }, (_, index) => {
    value = (value * 1664525 + 1013904223 + index * 17) >>> 0
    const normalized = (value % 1000) / 1000
    // A soft envelope so the skeleton reads as a waveform, not noise.
    return 0.12 + normalized * 0.72
  })
}

export default function UnifiedWaveform({
  peaks = [],
  colorBands = null,
  scale,
  currentTime = 0,
  duration = 0,
  inactive = false,
  variant = 'standard',
  status = 'idle',
  className = '',
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const measure = (rect: { width: number; height: number }) => {
      setSize((previous) => {
        const width = Math.max(0, Math.round(rect.width))
        const height = Math.max(0, Math.round(rect.height))
        return previous.width === width && previous.height === height ? previous : { width, height }
      })
    }
    measure(container.getBoundingClientRect())
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) measure(entry.contentRect)
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  const ready = status === 'ready' && peaks.length > 0
  const busy = BUSY_STATUSES.has(status as WaveformArtifactStatus | 'loading')
  const columns = size.width > 0 ? Math.max(1, Math.floor(size.width / BAR_STRIDE_PX)) : 0

  const bars = useMemo(
    () => (ready && columns > 0 ? buildWaveformBars(peaks, columns, scale) : []),
    [ready, peaks, columns, scale],
  )

  const colors = useMemo(() => {
    if (!ready || columns <= 0) return []
    return colorBands && colorBands.length > 0 ? downsampleColorBands(colorBands, columns) : []
  }, [ready, colorBands, columns])

  const progress = progressFraction(currentTime, duration)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !ready || size.width <= 0 || size.height <= 0) return
    const context = canvas.getContext('2d')
    if (!context) return

    const dpr = window.devicePixelRatio || 1
    const pixelWidth = Math.round(size.width * dpr)
    const pixelHeight = Math.round(size.height * dpr)
    if (canvas.width !== pixelWidth) canvas.width = pixelWidth
    if (canvas.height !== pixelHeight) canvas.height = pixelHeight
    context.setTransform(dpr, 0, 0, dpr, 0, 0)
    context.clearRect(0, 0, size.width, size.height)

    if (bars.length === 0) return

    const centerY = size.height / 2
    const halfHeight = size.height / 2
    const playedBars = Math.round(bars.length * progress)

    context.fillStyle = COLOR_CENTER_LINE
    context.fillRect(0, centerY - 0.5, size.width, 1)

    for (let index = 0; index < bars.length; index += 1) {
      const bar = bars[index]
      const top = centerY - bar.max * halfHeight
      const bottom = centerY - bar.min * halfHeight
      const height = Math.max(MIN_BAR_PX, bottom - top)
      const y = height === MIN_BAR_PX ? centerY - MIN_BAR_PX / 2 : top
      const amplitude = Math.max(Math.abs(bar.min), Math.abs(bar.max))
      const intensity = index < playedBars ? INTENSITY_PLAYED : INTENSITY_UPCOMING
      const color =
        colors.length > 0
          ? waveformFrequencyColor(colors[index][0], colors[index][1], colors[index][2], intensity)
          : waveformAmplitudeColor(amplitude, intensity)
      context.fillStyle = color
      context.fillRect(index * BAR_STRIDE_PX, y, BAR_WIDTH_PX, height)
    }

    // Playhead needle, visually consistent across all sizes.
    if (progress > 0) {
      const x = Math.min(size.width - 1, Math.round(progress * size.width))
      context.save()
      context.strokeStyle = COLOR_PLAYHEAD
      context.lineWidth = 1.5
      context.shadowColor = COLOR_PLAYHEAD
      context.shadowBlur = 6
      context.beginPath()
      context.moveTo(x + 0.5, 0)
      context.lineTo(x + 0.5, size.height)
      context.stroke()
      context.restore()
    }
  }, [bars, colors, progress, ready, size.height, size.width])

  const classes = [
    'unified-waveform',
    `unified-waveform--${variant}`,
    inactive ? 'unified-waveform--inactive' : '',
    ready ? 'unified-waveform--ready' : '',
    busy ? 'unified-waveform--busy' : '',
    className,
  ].filter(Boolean).join(' ')

  const skeleton = useMemo(
    () => skeletonHeights(0x9e3779b9, SKELETON_BARS[variant]),
    [variant],
  )

  return (
    <div
      ref={containerRef}
      className={classes}
      role="img"
      aria-label={
        ready
          ? 'Waveform of the current track'
          : busy
            ? 'Preparing waveform'
            : 'No waveform generated for this track'
      }
    >
      {ready && <canvas ref={canvasRef} aria-hidden="true" />}
      {!ready && busy && (
        <span className="unified-waveform-skeleton" aria-hidden="true">
          {skeleton.map((height, index) => (
            <span
              className="unified-waveform-skeleton-bar"
              key={index}
              style={{ height: `${Math.round(height * 100)}%` }}
            />
          ))}
        </span>
      )}
      {!ready && !busy && <span className="unified-waveform-empty-line" aria-hidden="true" />}
    </div>
  )
}
