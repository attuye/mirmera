/** Test-idea nudges beside the notes: what to try next, from what this session
 *  has done so far. Each says why it appeared and which heuristic it comes from.
 *  A suggestion goes into the note box; "Tried it" logs a test note; "Not now"
 *  hides the idea for the rest of the session. */
import { useCallback, useEffect, useState } from 'react'
import { api, Note, Nudge } from '../api/client'

const POLL_MS = 8000
const SHOWN = 3
const KIND_COLOR: Record<Nudge['kind'], string> = {
  problem: 'var(--danger)', risk: 'var(--warning)', idea: 'var(--info)', field: 'var(--accent)', choice: 'var(--accent)',
  upload: 'var(--accent)', submit: 'var(--accent)', page: 'var(--question)', time: 'var(--text-tertiary)',
}

/** The note "Tried it" logs: the idea itself for charter ideas and risks, else the title. */
function triedNote(n: Nudge): string {
  return n.kind === 'idea' || n.kind === 'risk' ? `Tried: ${n.suggestions[0]}` : `Tried: ${n.title}`
}

export default function TestIdeas({ sessionId, refreshKey, disabled, onNote, onUse }: {
  sessionId: string
  refreshKey: number  // changes when notes or bugs change, so covered ideas go away at once
  disabled: boolean
  onNote: (note: Note) => void
  onUse: (text: string) => void
}) {
  const [nudges, setNudges] = useState<Nudge[]>([])
  const [all, setAll] = useState(false)
  const [hidden, setHidden] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(() => api.listNudges(sessionId).then(setNudges).catch(() => {}), [sessionId])
  useEffect(() => { load() }, [load, refreshKey])
  useEffect(() => {
    if (disabled) return
    const id = window.setInterval(load, POLL_MS)
    return () => window.clearInterval(id)
  }, [load, disabled])

  async function act(n: Nudge, tried: boolean) {
    setBusy(n.id)
    try {
      if (tried) onNote(await api.triedNudge(sessionId, n.id, triedNote(n)))
      else await api.dismissNudge(sessionId, n.id)
      setNudges((list) => list.filter((x) => x.id !== n.id))
    } catch { /* stays; the next refresh tells */ } finally { setBusy(null) }
  }

  if (!nudges.length) return null
  const shown = all ? nudges : nudges.slice(0, SHOWN)
  return (
    <section aria-label="Test ideas" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
        <div className="mono" style={{ fontSize: 10.5, color: 'var(--text-quiet)', textTransform: 'uppercase', letterSpacing: 0.6 }}>
          Test ideas · {nudges.length}
        </div>
        <button onClick={() => setHidden(!hidden)} style={{ background: 'none', color: 'var(--text-tertiary)', fontSize: 11.5, padding: 0 }}>
          {hidden ? 'Show' : 'Hide'}
        </button>
      </div>
      {!hidden && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {shown.map((n) => (
            <article key={n.id} aria-label={n.title} style={{
              background: 'var(--panel)', border: '1px solid var(--border)', borderLeft: `3px solid ${KIND_COLOR[n.kind]}`,
              borderRadius: 9, padding: '10px 12px',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{n.title}</div>
                <span className="mono" title="The testing heuristic behind this idea"
                  style={{ fontSize: 10, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>{n.heuristic}</span>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 2 }}>{n.why}</div>
              {n.kind === 'problem' ? (
                <ul style={{ margin: '6px 0 0', paddingLeft: 16, fontSize: 11.5, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  {n.suggestions.map((s) => <li key={s}>{s}</li>)}
                </ul>
              ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                {n.suggestions.map((s) => (
                  <button key={s} onClick={() => onUse(`Tried ${s}: `)} disabled={disabled} title="Put it in the note box"
                    style={{ fontSize: 11.5, color: 'var(--text-secondary)', background: 'var(--panel-alt)', border: '1px solid var(--border-soft)',
                      borderRadius: 6, padding: '3px 8px', textAlign: 'left' }}>
                    {s}
                  </button>
                ))}
              </div>
              )}
              <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
                {n.kind === 'problem' && (
                  <button onClick={() => onUse(`Investigated ${n.title.replace(/^Investigate: /, '')}: `)} disabled={disabled}
                    style={{ background: 'none', padding: 0, fontSize: 11.5, color: 'var(--text-secondary)', fontWeight: 500 }}>Write a note</button>
                )}
                <button onClick={() => act(n, true)} disabled={disabled || busy === n.id}
                  style={{ background: 'none', padding: 0, fontSize: 11.5, color: 'var(--accent)', fontWeight: 500 }}>Tried it</button>
                <button onClick={() => act(n, false)} disabled={disabled || busy === n.id}
                  style={{ background: 'none', padding: 0, fontSize: 11.5, color: 'var(--text-tertiary)' }}>Not now</button>
              </div>
            </article>
          ))}
          {nudges.length > SHOWN && (
            <button onClick={() => setAll(!all)} style={{ background: 'none', padding: 0, fontSize: 11.5, color: 'var(--text-secondary)', alignSelf: 'flex-start' }}>
              {all ? 'Show fewer' : `Show ${nudges.length - SHOWN} more`}
            </button>
          )}
        </div>
      )}
    </section>
  )
}
