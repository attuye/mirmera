/** Isolate the bug: replay its steps with one recorded value changed at a time
 *  and show which changes make it go away. No AI: the variations come from the
 *  recording and the page. */
import { useEffect, useRef, useState } from 'react'
import { api, BugListItem, IsolationRun, IsolationSymptom, projectUrls, VariantResult } from '../api/client'
import { EnvironmentChoice } from './BrowserPanel'
import { useProject } from '../project'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

const POLL_MS = 1200

const RESULT: Record<VariantResult, { label: string; fg: string; bg: string }> = {
  gone: { label: 'Bug gone', fg: 'var(--accent)', bg: 'var(--accent-bg)' },
  bug: { label: 'Still fails', fg: 'var(--danger)', bg: 'var(--danger-bg)' },
  inconclusive: { label: 'Inconclusive', fg: 'var(--warning)', bg: 'var(--warning-bg)' },
  error: { label: 'Could not run', fg: 'var(--text-tertiary)', bg: 'var(--panel-alt)' },
}

const STATUS: Record<Exclude<IsolationRun['status'], 'running'>, string> = {
  done: 'Done', not_reproduced: 'The bug did not show', cancelled: 'Cancelled', error: 'Stopped',
}

function Badge({ result }: { result: VariantResult }) {
  const r = RESULT[result]
  return <span style={{ fontSize: 11, fontWeight: 600, color: r.fg, background: r.bg, padding: '2px 7px', borderRadius: 5, whiteSpace: 'nowrap' }}>{r.label}</span>
}

function Summary({ run }: { run: IsolationRun }) {
  const s = run.summary
  if (!s) return null
  return (
    <div aria-label="What the bug depends on" style={{ background: 'var(--panel-alt)', border: '1px solid var(--border)', borderRadius: 9, padding: '12px 14px', marginTop: 10, fontSize: 12.5, lineHeight: 1.6 }}>
      {s.depends_on.length > 0 ? s.depends_on.map((d) => (
        <div key={d.field}>
          <b style={{ color: 'var(--accent)' }}>Depends on {d.field}:</b>{' '}
          <span style={{ color: 'var(--text-secondary)' }}>the bug went away with {d.gone_with.join('; ')}.</span>
        </div>
      )) : (
        <div style={{ color: 'var(--text-secondary)' }}>
          <b style={{ color: 'var(--text)' }}>No single change made it go away.</b> It may depend on something the
          recording doesn't vary (the server, a date, an account), or on two things at once.
        </div>
      )}
      {s.irrelevant.length > 0 && <div style={{ color: 'var(--text-tertiary)' }}>Didn't matter: {s.irrelevant.join(', ')}.</div>}
      {s.inconclusive.length > 0 && (
        <div style={{ color: 'var(--text-tertiary)' }}>Inconclusive: {s.inconclusive.join(', ')} (changing it stopped the steps before the bug).</div>
      )}
    </div>
  )
}

export default function IsolationPanel({ bug }: { bug: BugListItem }) {
  const { project } = useProject()
  const environments = projectUrls(project)
  const [run, setRun] = useState<IsolationRun | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [mode, setMode] = useState<'auto' | IsolationSymptom['kind']>('auto')
  const [value, setValue] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    api.latestIsolation(bug.id).then((r) => { setRun(r); setLoaded(true) }).catch(() => setLoaded(true))
  }, [bug.id])
  useEffect(() => {
    if (run?.status !== 'running') return
    timer.current = window.setInterval(() => { api.getIsolation(run.id).then(setRun).catch(() => {}) }, POLL_MS)
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [run?.id, run?.status])

  async function start(e: React.FormEvent) {
    e.preventDefault()
    if (mode !== 'auto' && !value.trim()) { setError('Say what shows the bug.'); return }
    setBusy(true); setError(null)
    try {
      setRun(await api.startIsolation(bug.id, {
        base_url: baseUrl.trim() || undefined,
        symptom: mode === 'auto' ? undefined : { kind: mode, value: value.trim() },
      }))
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }

  if (!loaded) return null
  const running = run?.status === 'running'
  const small = { ...input, padding: '6px 9px', fontSize: 12.5 }

  return (
    <section aria-label="Isolate the bug" style={{ marginTop: 18, border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px' }}>
      <h3 style={{ fontSize: 13.5, fontWeight: 600, margin: 0 }}>Isolate the bug</h3>
      <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '4px 0 10px', lineHeight: 1.5 }}>
        Replays the steps to the bug once as they are, then once per variation, changing one recorded value at a time
        (another option, a plain or empty value, another date, a box left unticked), to find what the bug depends on.
      </p>

      {!running && (
        <form onSubmit={start}>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <label style={{ width: 230 }}>
              <span style={fieldLabel}>What shows the bug</span>
              <select value={mode} onChange={(e) => { setMode(e.target.value as typeof mode); setError(null) }} style={small}>
                <option value="auto">Automatic (from the recording)</option>
                <option value="failed_request">A request that fails</option>
                <option value="text">A text on the page</option>
              </select>
            </label>
            {mode !== 'auto' && (
              <label style={{ flex: 1, minWidth: 200 }}>
                <span style={fieldLabel}>{mode === 'text' ? 'Text' : 'Request URL contains'}</span>
                <input value={value} onChange={(e) => setValue(e.target.value)} style={small}
                  placeholder={mode === 'text' ? 'Something went wrong' : '/api/verify'} />
              </label>
            )}
            <label style={{ flex: 1, minWidth: 200 }}>
              <span style={fieldLabel}>App address</span>
              <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} style={small} placeholder="Where it was recorded" />
            </label>
            <button type="submit" disabled={busy} style={primaryButton(busy)}>{busy ? 'Starting…' : run ? 'Isolate again' : 'Isolate the bug'}</button>
          </div>
          {environments.length > 0 && <EnvironmentChoice environments={environments} current={baseUrl} onPick={setBaseUrl} />}
        </form>
      )}
      <ErrorLine message={error} />

      {run && (
        <div role="status" aria-label="Isolation result" style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: 12.5 }}>
            <b>{running ? `Running: variation ${run.variants.length} of ${run.total || '…'}` : STATUS[run.status as keyof typeof STATUS]}</b>
            <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
              bug shows as {run.symptom.kind === 'failed_request' ? `a failed request to ${run.symptom.value}` : `"${run.symptom.value}"`} · {run.base_url}
            </span>
            {running && <button onClick={() => api.cancelIsolation(run.id).then(setRun).catch((e) => setError(errorText(e)))} style={quietButton}>Cancel</button>}
          </div>
          {run.baseline && (
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>
              As recorded: <Badge result={run.baseline.result} /> {run.baseline.detail}
            </div>
          )}
          {run.detail && <div style={{ fontSize: 12.5, color: run.status === 'done' ? 'var(--text-secondary)' : 'var(--warning)', marginTop: 6 }}>{run.detail}</div>}
          <Summary run={run} />
          {run.variants.length > 0 && (
            <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10, fontSize: 12 }}>
              <thead>
                <tr style={{ color: 'var(--text-tertiary)', textAlign: 'left' }}>
                  <th style={{ fontWeight: 500, padding: '4px 6px' }}>Step</th>
                  <th style={{ fontWeight: 500, padding: '4px 6px' }}>Changed</th>
                  <th style={{ fontWeight: 500, padding: '4px 6px' }}>To</th>
                  <th style={{ fontWeight: 500, padding: '4px 6px' }}>Result</th>
                </tr>
              </thead>
              <tbody>
                {run.variants.map((v) => (
                  <tr key={v.n} aria-label={`${v.field}: ${v.change}`} title={v.detail} style={{ borderTop: '1px solid var(--border-soft)' }}>
                    <td className="mono" style={{ padding: '5px 6px', color: 'var(--text-quiet)' }}>{v.step}</td>
                    <td className="mono" style={{ padding: '5px 6px', color: 'var(--text-secondary)' }}>{v.field}</td>
                    <td style={{ padding: '5px 6px', color: 'var(--text-secondary)' }}>{v.change}</td>
                    <td style={{ padding: '5px 6px' }}><Badge result={v.result} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  )
}
