/** A project's regression tests together: run them all against one address,
 *  see what each result means for its bug, keep a history, export them. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import { EnvironmentChoice } from '../components/BrowserPanel'
import ClosedIssues from '../components/ClosedIssues'
import { utcDate } from '../components/Evidence'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from '../components/ui'
import { api, BROWSER_LABEL, BrowserEngine, projectUrls, suiteExportUrl, SuiteRun, SuiteTest, Triage } from '../api/client'
import { useProject, useProjectPath } from '../project'

const POLL_MS = 1500

/** Colour by what the result means for the bug, not by pass/fail: for a known bug,
 *  "reproduces" is the expected state and "passes" is the news. */
function tone(outcome: string | null, knownFailing: boolean): { fg: string; bg: string; label: string } {
  switch (outcome) {
    case 'reproduces':
      return knownFailing ? { fg: 'var(--text-secondary)', bg: 'var(--panel-alt)', label: 'Bug still there' }
        : { fg: 'var(--danger)', bg: 'var(--danger-bg)', label: 'Fails' }
    case 'passes':
      return knownFailing ? { fg: 'var(--accent)', bg: 'var(--accent-bg)', label: 'Now passes' }
        : { fg: 'var(--accent)', bg: 'var(--accent-bg)', label: 'Passes' }
    case 'broken': return { fg: 'var(--danger)', bg: 'var(--danger-bg)', label: 'Broken' }
    case 'misses_bug': return { fg: 'var(--danger)', bg: 'var(--danger-bg)', label: "Doesn't reach the bug" }
    case 'incomplete': return { fg: 'var(--warning)', bg: 'var(--warning-bg)', label: 'Incomplete' }
    case 'error': return { fg: 'var(--danger)', bg: 'var(--danger-bg)', label: 'Could not run' }
    default: return { fg: 'var(--text-quiet)', bg: 'transparent', label: 'Not replayed' }
  }
}

function Badge({ outcome, knownFailing }: { outcome: string | null; knownFailing: boolean }) {
  const t = tone(outcome, knownFailing)
  return <span style={{ fontSize: 11, fontWeight: 600, color: t.fg, background: t.bg, padding: '2px 7px', borderRadius: 5, whiteSpace: 'nowrap', border: outcome ? 'none' : '1px solid var(--border)' }}>{t.label}</span>
}

const TRIAGE_COLOR: Record<Triage['kind'], { fg: string; bg: string }> = {
  product: { fg: 'var(--danger)', bg: 'var(--danger-bg)' },
  test: { fg: 'var(--warning)', bg: 'var(--warning-bg)' },
  data: { fg: 'var(--warning)', bg: 'var(--warning-bg)' },
  setup: { fg: 'var(--warning)', bg: 'var(--warning-bg)' },
  environment: { fg: 'var(--info)', bg: 'var(--panel-alt)' },
  flaky: { fg: 'var(--question)', bg: 'var(--panel-alt)' },
}

/** Why it went red: the kind, the evidence, and what to do next. */
function TriageLine({ t, testId }: { t: Triage; testId: string }) {
  const c = TRIAGE_COLOR[t.kind]
  const [fixed, setFixed] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function fresh() {
    setBusy(true); setError(null)
    try {
      const r = await api.freshData(testId)
      const changed = r.changes.map((x) => `${x.field}: ${x.now}`).join(', ')
      setFixed(`${changed}. Replayed: ${r.test.replay?.outcome === 'passes' ? 'passes now' : r.test.replay?.outcome ?? 'not run'}.`)
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return (
    <div aria-label="Why" style={{ marginTop: 4, fontSize: 12 }}>
      <span style={{ fontSize: 10.5, fontWeight: 600, color: c.fg, background: c.bg, padding: '1px 6px', borderRadius: 5, marginRight: 6 }}>{t.label}</span>
      <span style={{ color: 'var(--text-secondary)' }}>{t.why}</span>
      <div style={{ color: 'var(--text-tertiary)', fontSize: 11.5, marginTop: 2 }}>Next: {t.next}</div>
      {t.kind === 'data' && !fixed && (
        <button onClick={fresh} disabled={busy} style={{ ...quietButton, fontSize: 11.5, padding: '3px 9px', marginTop: 4 }}
          title="The steps whose values the app said were used get fresh ones each run ({email}, {phone}, … {unique}); then it replays">
          {busy ? 'Replaying…' : 'Use fresh values'}</button>
      )}
      {fixed && <div role="status" style={{ color: 'var(--accent)', fontSize: 11.5, marginTop: 4 }}>Fresh values each run: {fixed}</div>}
      <ErrorLine message={error} />
    </div>
  )
}

function counts(run: SuiteRun): string {
  const c: Record<string, number> = {}
  for (const r of run.results) { const l = tone(r.outcome, r.known_failing).label; c[l] = (c[l] ?? 0) + 1 }
  return Object.entries(c).map(([l, n]) => `${n} ${l.toLowerCase()}`).join(' · ') || 'no results'
}

const when = (iso: string) => utcDate(iso).toLocaleString()

export default function Regression() {
  const { project } = useProject()
  const path = useProjectPath()
  const environments = projectUrls(project)
  const [tests, setTests] = useState<SuiteTest[] | null>(null)
  const [history, setHistory] = useState<SuiteRun[]>([])
  const [run, setRun] = useState<SuiteRun | null>(null)
  const [baseUrl, setBaseUrl] = useState(project.app_url ?? '')
  const [engine, setEngine] = useState<BrowserEngine | ''>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  const load = useCallback(async () => {
    const [t, h] = await Promise.all([api.listRegressionTests(project.id), api.suiteHistory(project.id)])
    setTests(t); setHistory(h)
    setRun((current) => current ?? h[0] ?? null)
  }, [project.id])
  useEffect(() => { load().catch((e) => setError(errorText(e))) }, [load])

  useEffect(() => {
    if (run?.status !== 'running') return
    timer.current = window.setInterval(() => {
      api.getSuiteRun(run.id).then((r) => { setRun(r); if (r.status !== 'running') load().catch(() => {}) }).catch(() => {})
    }, POLL_MS)
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [run?.id, run?.status, load])

  async function start(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setError(null)
    try { setRun(await api.runSuite(project.id, baseUrl.trim() || undefined, engine || undefined)) } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }

  const running = run?.status === 'running'
  const fixedNow = run && !running ? run.results.filter((r) => r.known_failing && r.outcome === 'passes') : []

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1100 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Regression tests</h1>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 640, lineHeight: 1.5 }}>
              The latest test of every reviewed bug in {project.name}. Run them all against one address to see which bugs
              are still there and which may be fixed.
            </p>
          </div>
          {tests && tests.length > 0 && (
            <a href={suiteExportUrl(project.id)} download style={{ ...quietButton, whiteSpace: 'nowrap', display: 'inline-block' }}>
              Export as Playwright project
            </a>
          )}
        </div>

        {tests && tests.length === 0 && (
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 22 }}>
            No regression tests yet. Generate one from a reviewed bug on the <Link to={path('/bugs')}>Bugs</Link> page.
          </p>
        )}

        {tests && tests.length > 0 && (
          <>
            <form onSubmit={start} aria-label="Run all" style={{ marginTop: 22, display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <label style={{ flex: 1, minWidth: 260, maxWidth: 480 }}>
                <span style={fieldLabel}>Replay against</span>
                <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} style={input} placeholder="Each test's recorded address" />
              </label>
              <label>
                <span style={fieldLabel}>In</span>
                <select aria-label="Browser" value={engine} onChange={(e) => setEngine(e.target.value as BrowserEngine | '')} style={input}>
                  <option value="">each bug's own browser</option>
                  {(Object.keys(BROWSER_LABEL) as BrowserEngine[]).map((b) => <option key={b} value={b}>{BROWSER_LABEL[b]}</option>)}
                </select>
              </label>
              <button type="submit" disabled={busy || running} style={primaryButton(busy || running)}>
                {running ? `Running ${run!.results.length + 1} of ${run!.total}…` : `Run all ${tests.length} test${tests.length === 1 ? '' : 's'}`}
              </button>
              {running && <button type="button" onClick={() => api.cancelSuiteRun(run!.id).then(setRun).catch((e) => setError(errorText(e)))} style={quietButton}>Cancel</button>}
            </form>
            {environments.length > 0 && <EnvironmentChoice environments={environments} current={baseUrl} onPick={setBaseUrl} />}
            <ErrorLine message={error} />

            {run && (
              <section aria-label="Latest run" style={{ marginTop: 22, border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px' }}>
                <div role="status" style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap', fontSize: 13 }}>
                  <b>{running ? 'Running' : run.status === 'done' ? 'Latest run' : run.status === 'cancelled' ? 'Cancelled' : 'Stopped'}</b>
                  <span style={{ color: 'var(--text-secondary)' }}>{counts(run)}</span>
                  <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{run.base_url ?? 'recorded addresses'}{run.browser ? ` · ${BROWSER_LABEL[run.browser]}` : ''} · {when(run.created_at)}</span>
                </div>
                {run.detail && <div style={{ fontSize: 12.5, color: 'var(--warning)', marginTop: 4 }}>{run.detail}</div>}
                {fixedNow.length > 0 && (
                  <div style={{ fontSize: 12.5, color: 'var(--accent)', marginTop: 6 }}>
                    {fixedNow.length} known bug{fixedNow.length === 1 ? '' : 's'} may be fixed: check, then remove test.fail() from {fixedNow.length === 1 ? 'its test' : 'their tests'}.
                  </div>
                )}
                <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10, fontSize: 12.5 }}>
                  <tbody>
                    {run.results.map((r) => (
                      <tr key={r.test_id} aria-label={r.bug_title} style={{ borderTop: '1px solid var(--border-soft)' }}>
                        <td style={{ padding: '6px', width: 160 }}><Badge outcome={r.outcome} knownFailing={r.known_failing} /></td>
                        <td style={{ padding: '6px' }}>
                          <div>{r.bug_title}</div>
                          <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{r.meaning}{r.detail ? ` · ${r.detail}` : ''}</div>
                          {r.triage && <TriageLine t={r.triage} testId={r.test_id} />}
                        </td>
                        <td className="mono" style={{ padding: '6px', fontSize: 11, color: 'var(--text-quiet)', textAlign: 'right' }}>{(r.duration_ms / 1000).toFixed(1)}s</td>
                      </tr>
                    ))}
                    {running && Array.from({ length: run.total - run.results.length }).map((_, i) => (
                      <tr key={`p${i}`} style={{ borderTop: '1px solid var(--border-soft)' }}>
                        <td colSpan={3} style={{ padding: '6px', color: 'var(--text-quiet)', fontSize: 12 }}>{i === 0 ? 'Replaying…' : 'Waiting'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}

            <section aria-label="Tests" style={{ marginTop: 26 }}>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 8px' }}>Tests</h2>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                <thead>
                  <tr style={{ color: 'var(--text-tertiary)', textAlign: 'left' }}>
                    {['Bug', 'File', 'Known bug', 'Last replay', ''].map((h) => <th key={h} style={{ fontWeight: 500, padding: '4px 6px' }}>{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {tests.map((t) => (
                    <tr key={t.test_id} aria-label={t.bug_title} style={{ borderTop: '1px solid var(--border-soft)' }}>
                      <td style={{ padding: '6px' }}>{t.bug_title}</td>
                      <td className="mono" style={{ padding: '6px', fontSize: 11.5, color: 'var(--text-secondary)' }}>{t.filename}</td>
                      <td style={{ padding: '6px', color: 'var(--text-secondary)' }}>{t.known_failing ? 'yes (test.fail)' : 'no'}</td>
                      <td style={{ padding: '6px' }} title={t.meaning ?? undefined}>
                        <Badge outcome={t.outcome} knownFailing={t.known_failing} />
                        {t.flaky && (
                          <span title={`${t.flaky.summary}; newest first: ${t.flaky.pattern}`} style={{ marginLeft: 6, fontSize: 10.5, fontWeight: 600,
                            color: TRIAGE_COLOR.flaky.fg, background: TRIAGE_COLOR.flaky.bg, padding: '2px 6px', borderRadius: 5 }}>
                            Flaky {t.flaky.pattern}
                          </span>
                        )}
                      </td>
                      <td className="mono" style={{ padding: '6px', fontSize: 11, color: 'var(--text-quiet)' }}>{t.replayed_at ? when(t.replayed_at) : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {history.length > 0 && (
              <section aria-label="History" style={{ marginTop: 26 }}>
                <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 8px' }}>History</h2>
                <ul style={{ margin: 0, padding: 0 }}>
                  {history.map((h) => (
                    <li key={h.id} style={{ listStyle: 'none', borderTop: '1px solid var(--border-soft)', padding: '6px 0', display: 'flex', gap: 12, fontSize: 12.5, alignItems: 'baseline' }}>
                      <button onClick={() => setRun(h)} style={{ background: 'none', padding: 0, color: 'var(--accent)', fontSize: 12.5 }}>{when(h.created_at)}</button>
                      <span style={{ color: 'var(--text-secondary)' }}>{counts(h)}</span>
                      <span className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)' }}>{h.base_url ?? 'recorded addresses'}{h.status !== 'done' ? ` · ${h.status}` : ''}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}

        {tests && <ClosedIssues projectId={project.id} baseUrl={baseUrl} />}
      </main>
    </div>
  )
}
