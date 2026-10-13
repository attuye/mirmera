/** What Suggest risks found for a story: the project's risks the change touches (link or drop each)
 *  and how many new risks it added to the story as proposals. See backend/app/story_risks.py. */
import { StoryRisksResult } from '../api/client'
import { quietButton } from './ui'

const small: React.CSSProperties = { ...quietButton, fontSize: 12, padding: '4px 10px' }

export default function SuggestedRisks({ result, busy, onDecide }: {
  result: StoryRisksResult; busy: boolean; onDecide: (riskId: string, action: 'link' | 'drop') => void
}) {
  const open = result.links.filter((l) => l.status === 'suggested')
  const n = result.new.length
  return (
    <section aria-label="Suggested risks" style={{ border: '1px solid var(--border)', borderLeft: '3px solid var(--info)', borderRadius: 9, padding: '12px 14px', marginBottom: 12, maxWidth: 820 }}>
      <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {n > 0
          ? <span>Added {n} new {n === 1 ? 'risk' : 'risks'} below as proposed ({result.new.map((r) => r.ref).join(', ')}). Accept the ones that matter and drop the rest.</span>
          : <span>No new risks: what this story changes is covered by the project's risks{open.length ? ' below' : ''}.</span>}
        {result.thin && <span role="note" aria-label="The story is thin" style={{ color: 'var(--warning)' }}>{result.thin}</span>}
      </div>
      {open.length > 0 && <>
        <h3 style={{ fontSize: 13, fontWeight: 600, margin: '12px 0 6px' }}>Project risks this story touches</h3>
        <ul aria-label="Project risks this story touches" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {open.map((l) => (
            <li key={l.risk_id} aria-label={`${l.ref} ${l.title}`} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', fontSize: 13, padding: '8px 12px', background: 'var(--panel)', borderRadius: 6 }}>
              <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', minWidth: 52, paddingTop: 2 }}>{l.ref} · {l.score}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 500 }}>{l.title}</div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>{l.reason}</div>
              </div>
              <button disabled={busy} onClick={() => onDecide(l.risk_id, 'link')} style={small} aria-label={`Link ${l.ref}`}>Link</button>
              <button disabled={busy} onClick={() => onDecide(l.risk_id, 'drop')} style={small} aria-label={`Drop ${l.ref}`}>Drop</button>
            </li>
          ))}
        </ul>
      </>}
    </section>
  )
}
