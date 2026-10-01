import { Risk } from '../api/client'

// Score bands for the impact x likelihood grid. The colours encode priority,
// so the matrix reads at a glance: where the dense, warm cells are is where to test first.
// `cell` is a stronger tint than `bg`, so the three bands stay distinct on the dark background.
export function scoreBand(score: number): { label: string; fg: string; bg: string; cell: string } {
  if (score >= 15) return { label: 'High', fg: 'var(--danger)', bg: 'var(--danger-bg)', cell: '#3B1D21' }
  if (score >= 8) return { label: 'Medium', fg: 'var(--warning)', bg: 'var(--warning-bg)', cell: '#33290F' }
  return { label: 'Low', fg: 'var(--text-secondary)', bg: 'var(--panel-alt)', cell: '#181A1F' }
}

const LEVELS = [1, 2, 3, 4, 5]

interface Props {
  risks: Risk[]
  selectedId: string | null
  onSelect: (risk: Risk) => void
}

/** 5x5 risk matrix: impact on the vertical axis, likelihood on the horizontal.
 *  Accepted risks are solid chips, proposed ones are outlined (still need review). */
export default function RiskMatrix({ risks, selectedId, onSelect }: Props) {
  const visible = risks.filter((r) => r.status !== 'retired')

  return (
    <figure style={{ margin: 0 }} aria-label="Risk matrix: impact by likelihood">
      <div style={{ display: 'grid', gridTemplateColumns: '18px repeat(5, 1fr)', gap: 3 }}>
        {[...LEVELS].reverse().map((impact) => (
          <div key={impact} style={{ display: 'contents' }}>
            <div className="mono" style={{ fontSize: 10, color: 'var(--text-quiet)', alignSelf: 'center', textAlign: 'right', paddingRight: 4 }}>
              {impact}
            </div>
            {LEVELS.map((likelihood) => {
              const band = scoreBand(impact * likelihood)
              const here = visible.filter((r) => r.impact === impact && r.likelihood === likelihood)
              return (
                <div
                  key={likelihood}
                  title={`Impact ${impact} × likelihood ${likelihood} = ${impact * likelihood}`}
                  style={{
                    background: band.cell, borderRadius: 4, minHeight: 44, padding: 4,
                    display: 'flex', flexWrap: 'wrap', alignContent: 'flex-start', gap: 3,
                  }}
                >
                  {here.map((r) => {
                    const selected = r.id === selectedId
                    const accepted = r.status === 'accepted'
                    return (
                      <button
                        key={r.id}
                        onClick={() => onSelect(r)}
                        aria-label={`${r.ref}: ${r.title}`}
                        title={`${r.ref}: ${r.title}`}
                        className="mono"
                        style={{
                          fontSize: 10, fontWeight: 600, padding: '2px 5px', borderRadius: 4,
                          background: accepted ? band.fg : 'transparent',
                          color: accepted ? 'var(--bg)' : band.fg,
                          border: `1px solid ${band.fg}`,
                          boxShadow: selected ? '0 0 0 2px var(--text)' : 'none',
                        }}
                      >
                        {r.ref}
                      </button>
                    )
                  })}
                </div>
              )
            })}
          </div>
        ))}
        <div />
        {LEVELS.map((l) => (
          <div key={l} className="mono" style={{ fontSize: 10, color: 'var(--text-quiet)', textAlign: 'center', paddingTop: 2 }}>{l}</div>
        ))}
      </div>
      <figcaption style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-tertiary)', marginTop: 8 }}>
        <span>↑ Impact</span>
        <span>Likelihood →</span>
      </figcaption>
      <div style={{ display: 'flex', gap: 12, fontSize: 11, color: 'var(--text-tertiary)', marginTop: 10 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 10, height: 10, borderRadius: 3, background: 'var(--text-secondary)' }} /> Accepted
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 10, height: 10, borderRadius: 3, border: '1px solid var(--text-secondary)' }} /> Needs review
        </span>
      </div>
    </figure>
  )
}
