/** "Let Mirmera explore": the exploration agent works through a charter in a headless
 *  browser on the project's app, records a session and drafts bugs for a tester to review.
 *  Shows its progress while it runs and what it left behind when it's done. */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bot } from 'lucide-react'
import { api, BROWSER_LABEL, BrowserEngine, ExplorationRun } from '../api/client'
import { useProjectPath } from '../project'
import { errorText, quietButton } from './ui'

/** The browser's clock: now, or a moment at the calendar's edge (the backend's clock presets). */
const CLOCKS: [string, string][] = [
  ['', 'at the real time'],
  ['after-midnight', 'just after midnight'],
  ['before-midnight', 'just before midnight'],
  ['pagume', 'on the last day of Pagume'],
  ['ethiopian-new-year', 'on Ethiopian New Year'],
  ['year-end', "on New Year's Eve"],
  ['leap-day', 'on 29 February'],
]

const small: React.CSSProperties = { ...quietButton, fontSize: 12, padding: '4px 10px' }

export default function ExploreButton({ charterId, onStarted, onFinished }: { charterId: string; onStarted?: () => void; onFinished?: () => void }) {
  const path = useProjectPath()
  const [run, setRun] = useState<ExplorationRun | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    api.charterExploration(charterId).then(setRun).catch(() => {})
    return () => { if (timer.current) window.clearTimeout(timer.current) }
  }, [charterId])
  useEffect(() => {  // follow a running exploration
    if (run?.status !== 'running') return
    timer.current = window.setTimeout(async () => {
      try {
        const next = await api.explorationRun(run.id)
        setRun(next)
        if (next.status !== 'running') onFinished?.()
      } catch (e) { setError(errorText(e)) }
    }, 2000)
  }, [run])  // eslint-disable-line react-hooks/exhaustive-deps

  const [as, setAs] = useState<'desktop' | 'phone' | 'phone-slow'>('desktop')
  const [clock, setClock] = useState('')
  const [engine, setEngine] = useState<BrowserEngine>('chromium')
  async function start() {
    setBusy(true); setError(null)
    const device = as === 'desktop' ? {} : { device: 'phone' as const, network: as === 'phone-slow' ? 'slow' as const : 'normal' as const }
    const conditions = as === 'desktop' && !clock && engine === 'chromium' ? undefined
      : { ...device, ...(clock ? { clock } : {}), ...(engine !== 'chromium' ? { browser: engine } : {}) }
    try { setRun(await api.exploreCharter(charterId, conditions ? { conditions } : {})); onStarted?.() } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  const actions = run ? run.steps.filter((s) => !['note', 'problem', 'done', 'invalid'].includes(s.kind)).length : 0
  const last = run?.steps[run.steps.length - 1]
  return (
    <div role="group" aria-label="Mirmera explores" style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 6, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      {run?.status === 'running' ? (
        <>
          <Bot size={13} aria-hidden style={{ color: 'var(--info)' }} />
          <span role="status" style={{ color: 'var(--info)' }}>Mirmera is exploring… {actions} action{actions === 1 ? '' : 's'}{last ? `, last: ${last.action}` : ''}</span>
          <button style={small} onClick={async () => { try { setRun(await api.cancelExploration(run.id)) } catch (e) { setError(errorText(e)) } }}>Stop</button>
        </>
      ) : (
        <>
          <button style={{ ...small, display: 'inline-flex', gap: 5, alignItems: 'center' }} disabled={busy} onClick={start}
            title="An AI explores this charter in a browser on the app and drafts bugs for you to review">
            <Bot size={13} aria-hidden />{busy ? 'Starting…' : run ? 'Explore again' : 'Let Mirmera explore'}
          </button>
          <select aria-label="Explore as" value={as} onChange={(e) => setAs(e.target.value as typeof as)}
            title="The device and network Mirmera's browser uses"
            style={{ fontSize: 11.5, padding: '3px 6px', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-secondary)' }}>
            <option value="desktop">on a desktop</option>
            <option value="phone">on a phone</option>
            <option value="phone-slow">on a phone, slow network</option>
          </select>
          <select aria-label="Browser clock" value={clock} onChange={(e) => setClock(e.target.value)}
            title="The day and time Mirmera's browser thinks it is, in Addis Ababa time: date bugs hide at the calendar's edges"
            style={{ fontSize: 11.5, padding: '3px 6px', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-secondary)' }}>
            {CLOCKS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <select aria-label="Browser" value={engine} onChange={(e) => setEngine(e.target.value as BrowserEngine)}
            title="The browser engine: users don't all use Chrome (Firefox and WebKit need: python -m playwright install firefox webkit)"
            style={{ fontSize: 11.5, padding: '3px 6px', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-secondary)' }}>
            {(Object.keys(BROWSER_LABEL) as BrowserEngine[]).map((b) => <option key={b} value={b}>in {BROWSER_LABEL[b]}</option>)}
          </select>
          {run && (
            <span role="status">
              {run.status === 'finished' ? 'Explored' : run.status === 'cancelled' ? 'Stopped' : 'Failed'}: {actions} actions,{' '}
              {run.bug_ids.length} bug draft{run.bug_ids.length === 1 ? '' : 's'} to review
              {run.status === 'error' && run.detail ? ` (${run.detail})` : ''}
              {run.session_id && <> · <Link to={path(`/sessions/${run.session_id}`)}>Open the session</Link></>}
              {run.bug_ids.length > 0 && <> · <Link to={path('/bugs')}>Review the bugs</Link></>}
            </span>
          )}
        </>
      )}
      {error && <span role="alert" style={{ color: 'var(--danger)' }}>{error}</span>}
    </div>
  )
}
