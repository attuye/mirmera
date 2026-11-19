/** Charter suggestions to review one by one: edit the mission, then Accept (it becomes a charter)
 *  or Drop. Only the ones still "suggested" are shown; accepted and dropped ones are remembered in
 *  the AI job's result, so leaving the page loses nothing. */
import { useEffect, useState } from 'react'
import MissionEditor from './MissionEditor'
import { quietButton } from './ui'

const PRIORITY_COLOR: Record<string, string> = { high: 'var(--danger)', medium: 'var(--warning)', low: 'var(--text-tertiary)' }

export interface DraftItem {
  mission: string
  rationale: string
  priority: 'high' | 'medium' | 'low'
  status: 'suggested' | 'accepted' | 'dropped'
  investigates?: string[]  // risk refs, for drafts planned from risks
  areas?: string[]         // risk areas, for suggestions from a change
  ideas?: string[]         // starting test ideas
}

export default function CharterDrafts({ jobId, items, busy, onAccept, onDrop }: {
  jobId: string
  items: DraftItem[]
  busy: boolean
  onAccept: (index: number, mission: string) => void
  onDrop: (index: number) => void
}) {
  const [missions, setMissions] = useState<Record<number, string>>({})  // edits before accepting
  useEffect(() => setMissions({}), [jobId])
  const open = items.map((d, i) => ({ d, i })).filter(({ d }) => d.status === 'suggested')
  if (open.length === 0) return null
  return (
    <ul aria-label="Suggested charters" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 820 }}>
      {open.map(({ d, i }) => {
        const mission = missions[i] ?? d.mission
        return (
          <li key={`${jobId}-${i}`} aria-label={mission} style={{ border: '1px solid var(--border)', borderRadius: 11, padding: '14px 16px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <MissionEditor value={mission} onSave={(m) => setMissions((prev) => ({ ...prev, [i]: m }))}
                  style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.4 }} />
              </div>
              <span className="mono" style={{ fontSize: 10, color: PRIORITY_COLOR[d.priority], flexShrink: 0, paddingTop: 3 }}>{d.priority.toUpperCase()}</span>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 8, lineHeight: 1.5 }}>{d.rationale}</div>
            {d.ideas && d.ideas.length > 0 && (
              <ul aria-label="Test ideas" style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                {d.ideas.slice(0, 4).map((idea) => <li key={idea}>{idea}</li>)}
              </ul>
            )}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, gap: 12 }}>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                {d.investigates && d.investigates.length > 0 && (
                  <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>investigates {d.investigates.join(', ')}</span>
                )}
                {(d.areas ?? []).map((r) => (
                  <span key={r} className="mono" style={{ fontSize: 9.5, color: 'var(--danger)', background: 'var(--danger-bg)', padding: '3px 7px', borderRadius: 5 }}>{r}</span>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
                <button disabled={busy} onClick={() => onDrop(i)} style={{ ...quietButton, fontSize: 12, padding: '5px 12px' }}
                  aria-label={`Drop: ${mission.slice(0, 60)}`}>Drop</button>
                <button disabled={busy} onClick={() => onAccept(i, mission)} aria-label={`Accept: ${mission.slice(0, 60)}`}
                  style={{ fontSize: 12, fontWeight: 600, color: '#0C0D0F', background: 'var(--accent)', padding: '5px 14px', borderRadius: 6 }}>
                  Accept
                </button>
              </div>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
