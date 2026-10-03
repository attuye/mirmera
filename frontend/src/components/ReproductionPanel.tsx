/** Reproduction agent on a bug: start it, follow its steps live, see what it
 *  proved (checked by Mirmera) and how reliably, turn the steps into a test. */
import { useEffect, useRef, useState } from 'react'
import { api, BugListItem, projectUrls, reproductionScreenshotUrl, ReproductionRun } from '../api/client'
import { EnvironmentChoice } from './BrowserPanel'
import { useProject } from '../project'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

const POLL_MS = 1500

const STEP_COLOR: Record<string, string> = {
  ok: 'var(--accent)', verified: 'var(--accent)', failed: 'var(--danger)', refused: 'var(--warning)',
  'not verified': 'var(--warning)', stopped: 'var(--text-tertiary)',
}

function verdict(run: ReproductionRun): { label: string; fg: string; bg: string } {
  if (run.status === 'running') return { label: 'Working…', fg: 'var(--info)', bg: 'var(--panel-alt)' }
  if (run.status === 'reproduced') {
    const r = run.reproducibility
    const label = r ? `Reproduced · ${r.reproduced} of ${r.runs} reruns` : 'Reproduced'
    const steady = r && r.reproduced === r.runs
    return { label, fg: steady ? 'var(--accent)' : 'var(--warning)', bg: steady ? 'var(--accent-bg)' : 'var(--warning-bg)' }
  }
  if (run.status === 'not_reproduced') return { label: 'Not reproduced', fg: 'var(--text-secondary)', bg: 'var(--panel-alt)' }
  if (run.status === 'cancelled') return { label: 'Cancelled', fg: 'var(--text-secondary)', bg: 'var(--panel-alt)' }
  return { label: 'Error', fg: 'var(--danger)', bg: 'var(--danger-bg)' }
}

export default function ReproductionPanel({ bug, onTestCreated }: { bug: BugListItem; onTestCreated: () => void }) {
  const [run, setRun] = useState<ReproductionRun | null>(null)
  const [loaded, setLoaded] = useState(false)
  const { project } = useProject()
  const environments = projectUrls(project)
  const [baseUrl, setBaseUrl] = useState('')
  const [maxSteps, setMaxSteps] = useState(20)
  const [runs, setRuns] = useState(3)
  const [busy, setBusy] = useState<null | 'start' | 'cancel' | 'test'>(null)
  const [error, setError] = useState<string | null>(null)
  const [testMade, setTestMade] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    api.latestReproduction(bug.id).then((r) => { setRun(r); setLoaded(true) }).catch(() => setLoaded(true))
  }, [bug.id])

  // Follow a running agent.
  useEffect(() => {
    if (run?.status !== 'running') return
    timer.current = window.setInterval(() => {
      api.getReproduction(run.id).then(setRun).catch(() => {})
    }, POLL_MS)
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [run?.id, run?.status])

  async function startRun() {
    setBusy('start'); setError(null); setTestMade(false)
    try {
      setRun(await api.startReproduction(bug.id, { base_url: baseUrl.trim() || undefined, max_steps: maxSteps, runs }))
    } catch (e) { setError(errorText(e)) } finally { setBusy(null) }
  }

  async function cancel() {
    if (!run) return
    setBusy('cancel')
    try { setRun(await api.cancelReproduction(run.id)) } catch (e) { setError(errorText(e)) } finally { setBusy(null) }
  }

  async function makeTest() {
    if (!run) return
    setBusy('test'); setError(null)
    try { await api.regressionFromReproduction(run.id); setTestMade(true); onTestCreated() }
    catch (e) { setError(errorText(e)) } finally { setBusy(null) }
  }

  if (!loaded) return null
  const v = run && verdict(run)
  const running = run?.status === 'running'

  return (
    <section aria-label="Reproduction agent" style={{
      marginTop: 14, border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', background: 'var(--panel)',
    }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap', marginBottom: 8 }}>
        <h3 style={{ fontSize: 13.5, fontWeight: 600, margin: 0 }}>Reproduction agent</h3>
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
          An AI tries to reproduce this bug in a headless browser, inside your app only. Mirmera checks what it claims.
        </span>
      </div>

      {!running && (
        <>
        <form onSubmit={(e) => { e.preventDefault(); startRun() }}
          style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <label style={{ flex: 1, minWidth: 220 }}>
            <span style={fieldLabel}>App address</span>
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} style={input}
              placeholder={run?.base_url || 'Where it was recorded, or e.g. http://localhost:4000'} />
          </label>
          <label style={{ width: 90 }}>
            <span style={fieldLabel}>Max steps</span>
            <input type="number" min={3} max={40} value={maxSteps} onChange={(e) => setMaxSteps(Number(e.target.value))} style={input} />
          </label>
          <label style={{ width: 90 }}>
            <span style={fieldLabel}>Reruns</span>
            <input type="number" min={1} max={10} value={runs} onChange={(e) => setRuns(Number(e.target.value))} style={input} />
          </label>
          <button type="submit" disabled={!!busy} style={primaryButton(!!busy)}>
            {busy === 'start' ? 'Starting…' : run ? 'Try again' : 'Reproduce with the agent'}
          </button>
        </form>
        {environments.length > 0 && <EnvironmentChoice environments={environments} current={baseUrl} onPick={setBaseUrl} />}
        </>
      )}

      {run && v && (
        <div style={{ marginTop: 12 }}>
          <div role="status" aria-label="Agent result" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: v.fg, background: v.bg, padding: '3px 9px', borderRadius: 6 }}>{v.label}</span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
              {running ? `step ${run.steps.length} of ${run.max_steps}` : `${run.steps.length} step${run.steps.length === 1 ? '' : 's'}`} · {run.start_url} · {run.ai_provider}/{run.ai_model}
            </span>
            {running && <button onClick={cancel} disabled={!!busy} style={{ ...quietButton, padding: '3px 10px' }}>Cancel</button>}
          </div>
          {run.detail && !running && <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 6 }}>{run.detail}</div>}
          {running && run.detail && <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 6 }}>{run.detail}</div>}

          {run.symptom && (
            <div style={{ fontSize: 12.5, marginTop: 8, padding: '8px 10px', borderRadius: 8, background: 'var(--panel-alt)', border: '1px solid var(--border)' }}>
              <div><strong>Proved:</strong> {run.symptom.explanation}</div>
              <div className="mono" style={{ fontSize: 11.5, color: 'var(--text-secondary)', marginTop: 3 }}>
                checked by Mirmera: {run.symptom.evidence}
              </div>
            </div>
          )}

          <ol aria-label="Agent steps" style={{ margin: '10px 0 0', padding: 0 }}>
            {run.steps.map((s) => (
              <li key={s.n} style={{ listStyle: 'none', fontSize: 12.5, padding: '5px 0', borderTop: '1px solid var(--border-soft)', display: 'flex', gap: 8 }}>
                <span className="mono" style={{ width: 22, color: 'var(--text-quiet)', flexShrink: 0 }}>{s.n}.</span>
                <span style={{ minWidth: 0, flex: 1 }}>
                  <span>{s.action}</span>
                  <span className="mono" style={{ fontSize: 11, color: STEP_COLOR[s.status] ?? 'var(--text-secondary)', marginLeft: 8 }}>{s.status}</span>
                  <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-tertiary)' }}>{s.thought}</span>
                  {s.detail && <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-secondary)', wordBreak: 'break-word' }}>{s.detail}</span>}
                </span>
              </li>
            ))}
          </ol>

          {run.reproducibility && (
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 8 }}>
              Reruns of the found steps: {run.reproducibility.results.map((r) => (r.reproduced ? '✓' : '✗')).join(' ')}
            </div>
          )}
          {run.has_screenshot && !running && (
            <a href={reproductionScreenshotUrl(run.id)} target="_blank" rel="noreferrer" style={{ display: 'inline-block', marginTop: 10 }}>
              <img src={reproductionScreenshotUrl(run.id)} alt="Where the agent stopped" width={240} style={{ borderRadius: 7, border: '1px solid var(--border)', display: 'block' }} />
            </a>
          )}
          {run.status === 'reproduced' && (
            <div style={{ marginTop: 10, display: 'flex', gap: 10, alignItems: 'center' }}>
              <button onClick={makeTest} disabled={!!busy || testMade} style={quietButton}>
                {busy === 'test' ? 'Creating…' : testMade ? 'Regression test created' : 'Make a regression test from these steps'}
              </button>
              {testMade && bug.kind !== 'bug' && (
                <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Review the bug as a bug to see it in the Regression test panel.</span>
              )}
            </div>
          )}
        </div>
      )}
      <ErrorLine message={error} />
    </section>
  )
}
