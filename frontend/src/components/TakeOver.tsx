import { useEffect, useState } from 'react'
import { Hand, MousePointerClick } from 'lucide-react'
import { api, type ExplorationRun } from '../api/client'
import { errorText } from './ui'

/** "Explore with me": take the agent's browser (a window on your screen) and hand it back. While it's
 *  yours, your clicks and typing are recorded in the run, with Mirmera's checks after each submit. */
export default function TakeOver({ run, onChange }: { run: ExplorationRun; onChange?: (r: ExplorationRun) => void }) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [mine, setMine] = useState<ExplorationRun['takeover']>(undefined)  // shown at once, before the next poll
  useEffect(() => setMine(undefined), [run.takeover?.state])
  if (run.status !== 'running' || !run.with_me) return null
  const state = (mine ?? run.takeover)?.state
  const yours = state === 'yours'
  const asked = state === 'asked'
  async function go(fn: () => Promise<ExplorationRun>) {
    setBusy(true); setError(null)
    try { const r = await fn(); setMine(r.takeover ?? null); onChange?.(r) } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const button = {
    fontSize: 11.5, padding: '4px 10px', borderRadius: 6, cursor: 'pointer', display: 'inline-flex', gap: 5, alignItems: 'center',
    border: '1px solid var(--accent)', background: yours ? 'var(--accent)' : 'transparent', color: yours ? 'white' : 'var(--accent)',
  } as const
  return (
    <div role="group" aria-label="Take over the browser" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 11.5 }}>
      {yours || asked
        ? <button style={button} disabled={busy} onClick={() => go(() => api.handBack(run.id))}
            title="Give the browser back: the agent carries on from this page, knowing what you did">
            <Hand size={12} aria-hidden />Hand back</button>
        : <button style={button} disabled={busy} onClick={() => go(() => api.takeOver(run.id))}
            title="The agent stops after its current step and the browser window is yours">
            <MousePointerClick size={12} aria-hidden />Take over</button>}
      <span role="status" style={{ color: yours ? 'var(--accent)' : 'var(--text-tertiary)' }}>
        {yours ? "It's your browser: use the Mirmera window on your screen. Your clicks and typing are recorded."
          : asked ? 'The agent stops after its current step…'
          : 'Its browser is a window on your screen.'}
      </span>
      {error && <span role="alert" style={{ color: 'var(--danger)' }}>{error}</span>}
    </div>
  )
}
