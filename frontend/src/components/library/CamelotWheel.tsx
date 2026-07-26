import { useId, useMemo } from 'react'
import { getCamelotHue, getCompatibleCamelotKeys, parseCamelotKey } from './libraryUtils'

interface Props {
  camelot: string | null | undefined
  /** Extra Camelot codes to mark on the wheel (e.g. keys actually present in the
   *  compatible-tracks API response), beyond the three standard relations. */
  compatibleKeys?: string[]
  size?: number
}

type WedgeRole = 'selected' | 'relative' | 'adjacent' | 'compatible' | 'neutral'

interface Wedge {
  code: string
  number: number
  letter: 'A' | 'B'
  role: WedgeRole
  path: string
  labelX: number
  labelY: number
}

const NUMBERS = Array.from({ length: 12 }, (_, i) => i + 1)
const RING = { innerA: 40, outerA: 68, innerB: 68, outerB: 96 }

function polar(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = ((angleDeg - 90) * Math.PI) / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}

function wedgePath(cx: number, cy: number, rInner: number, rOuter: number, startDeg: number, endDeg: number): string {
  const p1 = polar(cx, cy, rOuter, startDeg)
  const p2 = polar(cx, cy, rOuter, endDeg)
  const p3 = polar(cx, cy, rInner, endDeg)
  const p4 = polar(cx, cy, rInner, startDeg)
  const largeArc = endDeg - startDeg > 180 ? 1 : 0
  return [
    `M ${p1.x} ${p1.y}`,
    `A ${rOuter} ${rOuter} 0 ${largeArc} 1 ${p2.x} ${p2.y}`,
    `L ${p3.x} ${p3.y}`,
    `A ${rInner} ${rInner} 0 ${largeArc} 0 ${p4.x} ${p4.y}`,
    'Z',
  ].join(' ')
}

// Highlighted roles (selected/relative/adjacent) use opaque, saturated fills
// with dark text — verified >4.5:1 contrast — rather than light text on a
// translucent tint, which would drop below WCAG AA at this label size.
const ROLE_FILL: Record<WedgeRole, string> = {
  selected: 'hsl(190, 85%, 55%)',
  relative: 'hsl(258, 70%, 66%)',
  adjacent: 'hsl(190, 70%, 58%)',
  compatible: 'hsla(175, 60%, 55%, 0.22)',
  neutral: 'rgba(255,255,255,0.045)',
}

const ROLE_STROKE: Record<WedgeRole, string> = {
  selected: 'rgba(240,253,250,0.9)',
  relative: 'rgba(237,233,254,0.85)',
  adjacent: 'rgba(224,250,255,0.75)',
  compatible: 'rgba(94,234,212,0.4)',
  neutral: 'rgba(255,255,255,0.08)',
}

const ROLE_TEXT: Record<WedgeRole, string> = {
  selected: '#04211f',
  relative: '#1a0f33',
  adjacent: '#04211f',
  compatible: 'rgba(255,255,255,0.7)',
  neutral: 'rgba(255,255,255,0.4)',
}

/**
 * Real SVG Camelot wheel: 12 numbered positions x A(minor)/B(major) rings.
 * Highlights the selected key, its two adjacent wheel positions, its
 * relative major/minor, and any extra compatible keys the caller passes in.
 * Degrades to a neutral, fully-labeled wheel when there's no Camelot data.
 */
export default function CamelotWheel({ camelot, compatibleKeys = [], size = 176 }: Props) {
  const parsed = parseCamelotKey(camelot)
  const relations = getCompatibleCamelotKeys(camelot)
  const extraKeys = useMemo(() => new Set(compatibleKeys.map((k) => k.toUpperCase())), [compatibleKeys])

  const cx = 100
  const cy = 100

  function roleFor(code: string): WedgeRole {
    if (!relations) return 'neutral'
    if (code === relations.sameKey) return 'selected'
    if (code === relations.relative) return 'relative'
    if (relations.adjacent.includes(code)) return 'adjacent'
    if (extraKeys.has(code)) return 'compatible'
    return 'neutral'
  }

  const wedges: Wedge[] = useMemo(() => {
    const out: Wedge[] = []
    for (const n of NUMBERS) {
      const startDeg = (n - 1) * 30
      const endDeg = startDeg + 30
      const midDeg = startDeg + 15
      const codeA = `${n}A`
      const codeB = `${n}B`
      const labelA = polar(cx, cy, (RING.innerA + RING.outerA) / 2, midDeg)
      const labelB = polar(cx, cy, (RING.innerB + RING.outerB) / 2, midDeg)
      out.push({
        code: codeA,
        number: n,
        letter: 'A',
        role: roleFor(codeA),
        path: wedgePath(cx, cy, RING.innerA, RING.outerA, startDeg, endDeg),
        labelX: labelA.x,
        labelY: labelA.y,
      })
      out.push({
        code: codeB,
        number: n,
        letter: 'B',
        role: roleFor(codeB),
        path: wedgePath(cx, cy, RING.innerB, RING.outerB, startDeg, endDeg),
        labelX: labelB.x,
        labelY: labelB.y,
      })
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camelot, compatibleKeys.join(',')])

  const hue = getCamelotHue(camelot)
  const descriptionId = useId()

  const ariaLabel = parsed && relations ? `Camelot wheel, selected key ${relations.sameKey}` : 'Camelot wheel, no key selected'

  const description = useMemo(() => {
    if (!parsed || !relations) return 'No Camelot key selected for this track.'
    return (
      `Selected key ${relations.sameKey}. ` +
      `Adjacent keys ${relations.adjacent.join(' and ')}. ` +
      `Relative major/minor ${relations.relative}.`
    )
  }, [parsed, relations])

  return (
    <div className="lib-camelot-wheel" style={{ width: size, height: size }}>
      <svg
        viewBox="0 0 200 200"
        width={size}
        height={size}
        role="img"
        aria-label={ariaLabel}
        aria-describedby={descriptionId}
      >
        {wedges.map((w) => (
          <path
            key={w.code}
            d={w.path}
            fill={ROLE_FILL[w.role]}
            stroke={ROLE_STROKE[w.role]}
            strokeWidth={w.role === 'selected' ? 1.5 : 1}
          />
        ))}
        {wedges.map((w) => (
          <text
            key={`${w.code}-label`}
            x={w.labelX}
            y={w.labelY}
            textAnchor="middle"
            dominantBaseline="middle"
            fontSize={w.role === 'selected' ? 8.5 : 7.5}
            fontWeight={w.role === 'selected' ? 700 : 500}
            fill={ROLE_TEXT[w.role]}
          >
            {w.code}
          </text>
        ))}
        <circle cx={cx} cy={cy} r={38} fill="rgba(8,12,20,0.85)" stroke="rgba(255,255,255,0.08)" />
        <text
          x={cx}
          y={cy - 4}
          textAnchor="middle"
          fontSize={parsed ? 20 : 12}
          fontWeight={800}
          fill={hue !== null ? `hsl(${hue} 80% 72%)` : 'rgba(255,255,255,0.4)'}
        >
          {parsed ? relations?.sameKey : '—'}
        </text>
        <text x={cx} y={cy + 12} textAnchor="middle" fontSize={7} fontWeight={700} letterSpacing="0.08em" fill="rgba(255,255,255,0.4)">
          {parsed ? 'CAMELOT' : 'NO KEY'}
        </text>
      </svg>
      <span id={descriptionId} className="lib-visually-hidden">{description}</span>
    </div>
  )
}
