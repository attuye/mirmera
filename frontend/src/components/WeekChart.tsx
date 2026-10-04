/** A small weekly chart in plain SVG: columns, stacked columns, 100% stacked
 *  columns, or a line. Thin marks, rounded data-ends, a 2px surface gap between
 *  stacked segments, a hover band with a tooltip, a legend for two or more series. */
import { useState } from 'react'

export interface Series { key: string; label: string; color: string; values: (number | null)[] }

const W = 560, H = 190, M = { l: 38, r: 10, t: 10, b: 24 }
const PW = W - M.l - M.r, PH = H - M.t - M.b

function niceMax(v: number): number {
  if (v <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  const n = v / p
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p
}

const weekLabel = (iso: string) => new Date(iso + 'T00:00:00Z').toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })

/** A column whose top corners are rounded (the data-end), square at the baseline. */
function column(x: number, y: number, w: number, h: number, round: boolean): string {
  const r = round ? Math.min(4, h, w / 2) : 0
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`
}

export default function WeekChart({ title, note, weeks, series, kind, format = (v) => `${v}`, extra, integer = false }: {
  title: string; note?: string; weeks: string[]; series: Series[]
  kind: 'columns' | 'stacked' | 'percent' | 'line'
  format?: (v: number) => string
  integer?: boolean  // counts: whole-number ticks only
  extra?: (i: number) => string[]  // more tooltip lines for a week
}) {
  const [hover, setHover] = useState<number | null>(null)
  const n = weeks.length
  const band = PW / n
  const barW = Math.min(24, band * 0.6)
  const totals = weeks.map((_, i) => series.reduce((s, x) => s + (x.values[i] ?? 0), 0))
  const raw = kind === 'percent' ? 1 : niceMax(kind === 'stacked' ? Math.max(0, ...totals) : Math.max(0, ...series.flatMap((s) => s.values.map((v) => v ?? 0))))
  const max = integer ? Math.max(2, Math.ceil(raw / 2) * 2) : raw  // counts: an even top, so the middle tick is whole too
  const y = (v: number) => M.t + PH - (v / max) * PH
  const ticks = [0, max / 2, max]
  const every = n > 16 ? 4 : n > 8 ? 2 : 1
  const empty = series.every((s) => s.values.every((v) => !v))

  return (
    <figure aria-label={title} style={{ margin: 0, background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, padding: '12px 14px', position: 'relative' }}>
      <figcaption>
        <div style={{ fontSize: 13.5, fontWeight: 600 }}>{title}</div>
        {note && <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 2 }}>{note}</div>}
      </figcaption>
      {series.length > 1 && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 6, fontSize: 11, color: 'var(--text-secondary)' }}>
          {series.map((s) => (
            <span key={s.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <i style={{ width: 9, height: 9, borderRadius: 2, background: s.color }} />{s.label}
            </span>
          ))}
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`${title}, week by week`} style={{ display: 'block', marginTop: 6 }}
        onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} stroke="var(--border-soft)" strokeWidth={1} />
            <text x={M.l - 6} y={y(t) + 3} textAnchor="end" fontSize={10} fill="var(--text-quiet)">
              {kind === 'percent' ? `${Math.round(t * 100)}%` : format(t)}
            </text>
          </g>
        ))}
        {hover !== null && <rect x={M.l + hover * band} y={M.t} width={band} height={PH} fill="var(--panel-alt)" />}

        {kind !== 'line' && weeks.map((_, i) => {
          const cx = M.l + i * band + (band - barW) / 2
          if (kind === 'columns') {
            const v = series[0].values[i] ?? 0
            return v > 0 ? <path key={i} d={column(cx, y(v), barW, M.t + PH - y(v), true)} fill={series[0].color} /> : null
          }
          const total = totals[i]
          if (!total) return null
          let base = 0
          const parts = series.map((s) => ({ s, v: (s.values[i] ?? 0) / (kind === 'percent' ? total : 1) })).filter((p) => p.v > 0)
          return (
            <g key={i}>
              {parts.map((p, j) => {
                const top = y(base + p.v), h = y(base) - top
                base += p.v
                const gap = j < parts.length - 1 ? 2 : 0  // the surface gap between stacked segments
                return <path key={p.s.key} d={column(cx, top + gap, barW, Math.max(0, h - gap), j === parts.length - 1)} fill={p.s.color} />
              })}
            </g>
          )
        })}

        {kind === 'line' && series.map((s) => {
          const pts = s.values.map((v, i) => (v === null ? null : [M.l + i * band + band / 2, y(v)] as const))
          const runs: string[] = []
          let cur = ''
          pts.forEach((p) => { if (p) cur += `${cur ? 'L' : 'M'}${p[0]},${p[1]} `; else if (cur) { runs.push(cur); cur = '' } })
          if (cur) runs.push(cur)
          return (
            <g key={s.key}>
              {runs.map((d) => <path key={d} d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />)}
              {pts.map((p, i) => p && <circle key={i} cx={p[0]} cy={p[1]} r={4} fill={s.color} stroke="var(--panel)" strokeWidth={2} />)}
            </g>
          )
        })}

        {weeks.map((w, i) => i % every === 0 && (
          <text key={w} x={M.l + i * band + band / 2} y={H - 6} textAnchor="middle" fontSize={10} fill="var(--text-quiet)">{weekLabel(w)}</text>
        ))}
        {weeks.map((w, i) => (
          <rect key={`hit${w}`} x={M.l + i * band} y={M.t} width={band} height={PH} fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
        {empty && <text x={M.l + PW / 2} y={M.t + PH / 2} textAnchor="middle" fontSize={12} fill="var(--text-tertiary)">Nothing recorded yet</text>}
      </svg>
      {hover !== null && (
        <div role="tooltip" style={{
          position: 'absolute', top: 54, left: `${Math.min(70, Math.max(4, ((M.l + hover * band + band) / W) * 100))}%`,
          background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8, padding: '7px 10px', fontSize: 11.5,
          pointerEvents: 'none', minWidth: 130, boxShadow: '0 6px 18px rgba(0,0,0,.4)', zIndex: 2,
        }}>
          <div style={{ color: 'var(--text-tertiary)', marginBottom: 3 }}>Week of {weekLabel(weeks[hover])}</div>
          {series.map((s) => (
            <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <i style={{ width: 8, height: 8, borderRadius: 2, background: s.color }} />
              <span style={{ color: 'var(--text-secondary)', flex: 1 }}>{s.label}</span>
              <span className="mono">{s.values[hover] === null ? '–' : kind === 'percent' ? `${Math.round(((s.values[hover] ?? 0) / (totals[hover] || 1)) * 100)}%` : format(s.values[hover] ?? 0)}</span>
            </div>
          ))}
          {extra?.(hover).map((line) => <div key={line} style={{ color: 'var(--text-tertiary)' }}>{line}</div>)}
        </div>
      )}
    </figure>
  )
}
