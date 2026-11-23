import { useEffect, useState } from 'react'
import { api, type AgentRecord as Record, type AgentTally } from '../api/client'

/** What testers made of the exploration agent's drafts, so you know how far to trust them:
 *  overall, and split by who decided a draft was a problem (Mirmera's own checks, or the AI). */
export default function AgentRecord({ projectId, refresh }: { projectId: string; refresh: number }) {
  const [rec, setRec] = useState<Record | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => { api.agentRecord(projectId).then(setRec).catch(() => setRec(null)) }, [projectId, refresh])
  if (!rec || !rec.overall.drafts) return null
  const o = rec.overall
  return (
    <section aria-label="The agent's track record" style={{ border: '1px solid var(--border)', borderRadius: 10, background: 'var(--panel)',
      padding: '10px 14px', marginBottom: 16, fontSize: 12.5 }}>
      <button aria-expanded={open} onClick={() => setOpen(!open)} style={{ border: 'none', background: 'transparent', padding: 0,
        cursor: 'pointer', textAlign: 'left', color: 'var(--text-primary)', fontFamily: 'var(--font-ui)', fontSize: 12.5, width: '100%' }}>
        <strong style={{ fontWeight: 600 }}>The agent's track record</strong>
        <span style={{ color: 'var(--text-secondary)' }}> · {summary(o)}</span>
        <span style={{ float: 'right', color: 'var(--text-tertiary)' }}>{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div style={{ marginTop: 10 }}>
          <table style={{ borderCollapse: 'collapse', width: '100%' }}>
            <thead><tr style={{ color: 'var(--text-tertiary)', textAlign: 'left' }}>
              {['Who decided it was a problem', 'Drafts', 'Bugs', 'Questions', 'Dismissed', 'Waiting'].map((h) => (
                <th key={h} style={{ fontWeight: 500, padding: '4px 8px 4px 0' }}>{h}</th>))}
            </tr></thead>
            <tbody>
              {rec.by_judge.map((j) => (
                <tr key={j.judge} aria-label={j.label} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '5px 8px 5px 0' }}>{j.label.charAt(0).toUpperCase() + j.label.slice(1)}</td>
                  {[j.drafts, j.bugs, j.questions, j.dismissed, j.waiting].map((n, i) => <td key={i} className="mono" style={{ padding: '5px 8px 5px 0' }}>{n}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          {Object.keys(o.dismissed_for).length > 0 && (
            <div style={{ marginTop: 8, color: 'var(--text-secondary)' }}>
              Dismissed as: {Object.entries(o.dismissed_for).map(([why, n]) => `${why} (${n})`).join(', ')}
            </div>
          )}
          {o.replayed > 0 && (
            <div style={{ marginTop: 4, color: 'var(--text-secondary)' }}>
              Replayed in fresh browsers: {o.every_time} of {o.replayed} showed every time{o.never_again ? `, ${o.never_again} never showed again` : ''}.
            </div>
          )}
          <div style={{ marginTop: 8, color: 'var(--text-tertiary)' }}>
            Mirmera's own checks are plain code (failing requests, display versus data, privacy, layout, its own reading of rules).
            The AI's judgement is where it read the app's answer and decided a rule was broken, or reported a problem itself.
          </div>
        </div>
      )}
    </section>
  )
}

function summary(o: AgentTally): string {
  const decided = o.bugs + o.questions + o.dismissed
  const parts = [`${o.drafts} draft${o.drafts === 1 ? '' : 's'}`]
  if (decided) parts.push(`${o.bugs} of ${decided} reviewed kept as bugs`)
  if (o.waiting) parts.push(`${o.waiting} waiting for review`)
  return parts.join(', ')
}
