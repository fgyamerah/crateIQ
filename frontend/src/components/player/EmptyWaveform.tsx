/**
 * Truthful "no waveform" visual.
 *
 * Replaces the old ThreeBandWaveform placeholder in surfaces that present the
 * real waveform lifecycle (Track Inspector, Persistent Player). Unlike that
 * component, this renders nothing that could be mistaken for analyzed audio —
 * no bands, no per-track deterministic bars, no color. Just a thin muted
 * center line, matching the center line drawn on a real ready waveform.
 */
interface Props {
  inactive?: boolean
  className?: string
}

export default function EmptyWaveform({ inactive = false, className = '' }: Props) {
  const classes = [
    'empty-waveform',
    inactive ? 'empty-waveform--inactive' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <div className={classes} role="img" aria-label="No waveform generated for this track">
      <span className="empty-waveform-line" aria-hidden="true" />
    </div>
  )
}
