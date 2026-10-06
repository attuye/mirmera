/** "Let Mirmera explore": the exploration agent works through a charter in a headless
 *  browser on the project's app, records a session and drafts bugs for a tester to review.
 *  Shows its progress while it runs and what it left behind when it's done. */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bot } from 'lucide-react'
import { api, ExplorationRun } from '../api/client'
import { useProjectPath } from '../project'
import { errorText, quietButton } from './ui'

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

  async function start() {
    setBusy(true); setError(null)
    try { setRun(await api.exploreCharter(charterId)); onStarted?.() } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
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
