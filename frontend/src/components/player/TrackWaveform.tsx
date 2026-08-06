import { useEffect, useMemo, useRef, useState } from 'react'
import { buildWaveformBars, progressFraction } from './waveformGeometry'

/**
 * Real waveform renderer.
 *
 * Pure presentation: it receives peaks and playback position as props and
 * draws them. It never fetches, generates, owns job state, or touches audio.
 *
 * Non-interactive in W4 — it is exposed as an image, not a slider, and is not
 * focusable. Interactive seeking and slider semantics belong to W5; the
 * player's existing range input remains the accessible seek control.
 */

/**
 * Column pitch in CSS pixels. Kept tight so the ~1024 available peak pairs
 * read as a waveform envelope rather than a sparse barcode; the fractional
 * bar width keeps a hairline of separation without gapping.
 */
const BAR_STRIDE_PX = 2
const BAR_WIDTH_PX = 1.5
/** Keep silence visible as a thin center line rather than nothing. */
const MIN_BAR_PX = 1

const COLOR_UNPLAYED = 'rgba(146, 173, 199, 0.62)'
const COLOR_PLAYED = '#20d4d8'
const COLOR_CENTER_LINE = 'rgba(133, 158, 184, 0.22)'

interface Props {
  peaks: readonly number[]
  scale: number
  /** Seconds elapsed, from the persistent player. */
  currentTime: number
  /** Seconds total, from the persistent player. */
  duration: number
  /** Dims the waveform when playback is not active. */
  inactive?: boolean
  className?: string
}

export default function TrackWaveform({
  peaks,
  scale,
  currentTime,
  duration,
  inactive = false,
  className = '',
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  // Track the rendered CSS box so the canvas can stay pixel-sharp.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const measure = (rect: { width: number; height: number }) => {
      setSize((previous) => {
        const width = Math.max(0, Math.round(rect.width))
        const height = Math.max(0, Math.round(rect.height))
        return previous.width === width && previous.height === height
          ? previous
          : { width, height }
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

  const columns = size.width > 0 ? Math.max(1, Math.floor(size.width / BAR_STRIDE_PX)) : 0

  // Geometry depends only on the peaks and the column count, so it survives
  // every playback time update instead of being rebuilt ~4x per second.
  const bars = useMemo(
    () => (columns > 0 ? buildWaveformBars(peaks, columns, scale) : []),
    [peaks, columns, scale],
  )

  const progress = progressFraction(currentTime, duration)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || size.width <= 0 || size.height <= 0) return
    const context = canvas.getContext('2d')
    if (!context) return

    const dpr = window.devicePixelRatio || 1
    const pixelWidth = Math.round(size.width * dpr)
    const pixelHeight = Math.round(size.height * dpr)
    if (canvas.width !== pixelWidth) canvas.width = pixelWidth
    if (canvas.height !== pixelHeight) canvas.height = pixelHeight
    // Draw in CSS pixels; the transform keeps lines crisp on HiDPI displays.
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
      context.fillStyle = index < playedBars ? COLOR_PLAYED : COLOR_UNPLAYED
      context.fillRect(index * BAR_STRIDE_PX, y, BAR_WIDTH_PX, height)
    }
  }, [bars, progress, size.height, size.width])

  const classes = [
    'track-waveform',
    inactive ? 'track-waveform--inactive' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <div
      ref={containerRef}
      className={classes}
      role="img"
      aria-label="Waveform of the current track"
    >
      <canvas ref={canvasRef} aria-hidden="true" />
    </div>
  )
}
