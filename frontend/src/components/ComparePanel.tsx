/** Compare two versions or environments: the session's journey done again on both, side by
 *  side, step by step. Mirmera lists what differs (no AI), with screenshots, and a difference
 *  can become a bug draft with the other environment as its oracle. */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, BROWSER_LABEL, BrowserEngine, Comparison, comparisonShotUrl, Environment, Project, projectUrls } from '../api/client'

type Side = Environment & { browser: BrowserEngine }
import { useProjectPath } from '../project'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

const box: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'var(--panel)' }

function EnvPicker({ label, value, onChange, environments }: {
  label: string; value: Side; onChange: (e: Side) => void; environments: Environment[]
}) {
  return (
    <fieldset style={{ border: 'none', padding: 0, margin: 0, flex: 1, minWidth: 0 }}>
      <legend style={fieldLabel}>{label}</legend>
      <input aria-label={`${label} name`} value={value.name} placeholder="name, e.g. production"
        onChange={(e) => onChange({ ...value, name: e.target.value })} style={{ ...input, marginBottom: 6 }} />
      <input aria-label={`${label} address`} value={value.url} placeholder="https://…"
        onChange={(e) => onChange({ ...value, url: e.target.value })} style={{ ...input, marginBottom: 6 }} />
      <select aria-label={`${label} browser`} value={value.browser} onChange={(e) => onChange({ ...value, browser: e.target.value as BrowserEngine })} style={input}>
        {(Object.keys(BROWSER_LABEL) as BrowserEngine[]).map((b) => <option key={b} value={b}>{BROWSER_LABEL[b]}</option>)}
      </select>
      {environments.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
          {environments.map((e) => (
            <button key={e.url} type="button" title={e.url} aria-pressed={value.url === e.url}
              onClick={() => onChange({ ...value, name: e.name === 'default' ? 'app' : e.name, url: e.url })}
              style={{ ...quietButton, fontSize: 11, padding: '3px 8px' }}>{e.name}</button>
          ))}
        </div>
      )}
    </fieldset>
  )
}

export default function ComparePanel({ sessionId, project }: { sessionId: string; project: Project | null }) {
  const path = useProjectPath()
  const environments = project ? projectUrls(project) : []
  const [a, setA] = useState<Side>(() => environments[0] ? { name: environments[0].name === 'default' ? 'app' : environments[0].name, url: environments[0].url, browser: 'chromium' } : { name: '', url: '', browser: 'chromium' })
  const [b, setB] = useState<Side>(() => environments[1] ? { ...environments[1], browser: 'chromium' } : { name: '', url: '', browser: 'chromium' })
  const [result, setResult] = useState<Comparison | null>(null)
  const [busy, setBusy] = useState(false)
  const [drafting, setDrafting] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [onlyDiff, setOnlyDiff] = useState(true)

  useEffect(() => {  // the project loads after the page: start from its first two addresses
    if (!a.url && environments[0]) setA({ name: environments[0].name === 'default' ? 'app' : environments[0].name, url: environments[0].url, browser: a.browser })
    if (!b.url && environments[1]) setB({ ...environments[1], browser: b.browser })
  }, [project?.id])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.comparisons(sessionId).then((list) => setResult(list[0] ?? null)).catch(() => {}) }, [sessionId])

  async function run() {
    setBusy(true); setError(null)
    try { setResult(await api.compareSession(sessionId, a, b)) } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  async function draft(n: number, right: 'a' | 'b') {
    if (!result) return
    setDrafting(n); setError(null)
    try { setResult(await api.comparisonBug(result.id, n, right)) } catch (e) { setError(errorText(e)) } finally { setDrafting(null) }
  }

  const steps = result ? result.steps.filter((s) => !onlyDiff || s.differences.length) : []
  return (
    <section aria-label="Compare environments" style={{ width: 360, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={box}>
        <p style={{ margin: '0 0 10px', fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.45 }}>
          Do this session's steps again on two versions or environments (or one address in two browsers), side by side, and see what differs.
        </p>
        <div style={{ display: 'flex', gap: 10 }}>
          <EnvPicker label="A" value={a} onChange={setA} environments={environments} />
          <EnvPicker label="B" value={b} onChange={setB} environments={environments} />
        </div>
        <button onClick={run} disabled={busy || !a.url || !b.url || !a.name || !b.name}
          style={{ ...primaryButton(busy), marginTop: 10, width: '100%' }}>
          {busy ? 'Comparing… (both browsers, step by step)' : 'Compare'}
        </button>
        <ErrorLine message={error} />
      </div>

      {result && (
        <div style={box} aria-label="Comparison result" role="region">
          <div role="status" style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6 }}>
            {result.status === 'error' ? `Couldn't finish: ${result.detail}` : result.summary}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginBottom: 8 }}>
            A = {result.env_a.name} ({result.env_a.url}{result.env_a.browser && result.env_a.browser !== 'chromium' ? `, ${BROWSER_LABEL[result.env_a.browser]}` : ''}) ·
            B = {result.env_b.name} ({result.env_b.url}{result.env_b.browser && result.env_b.browser !== 'chromium' ? `, ${BROWSER_LABEL[result.env_b.browser]}` : ''})
          </div>
          {result.bug_ids.length > 0 && (
            <div style={{ fontSize: 12, marginBottom: 8 }}>
              {result.bug_ids.length} bug draft{result.bug_ids.length === 1 ? '' : 's'} from this comparison · <Link to={path('/bugs')}>Review the bugs</Link>
            </div>
          )}
          <label style={{ fontSize: 11.5, color: 'var(--text-secondary)', display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8 }}>
            <input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> Only the steps that differ
          </label>
          <ol aria-label="Steps compared" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {steps.map((s) => (
              <li key={s.n} style={{ borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                <div style={{ fontSize: 12.5 }}><span className="mono" style={{ color: 'var(--text-quiet)' }}>{s.n}</span> {s.description}</div>
                {s.differences.length === 0 ? (
                  <div style={{ fontSize: 11.5, color: 'var(--accent)' }}>The same on both</div>
                ) : (
                  <>
                    <ul aria-label={`Differences at step ${s.n}`} style={{ margin: '4px 0', paddingLeft: 16, fontSize: 12, color: 'var(--warning)' }}>
                      {s.differences.map((d) => <li key={d}>{d}</li>)}
                    </ul>
                    {s.shots.length > 0 && (
                      <div style={{ display: 'flex', gap: 6 }}>
                        {s.shots.map((name) => (
                          <a key={name} href={comparisonShotUrl(result.id, name)} target="_blank" rel="noreferrer" style={{ flex: 1 }}>
                            <img src={comparisonShotUrl(result.id, name)} alt={`Step ${s.n} on ${name.endsWith('a.jpg') ? result.env_a.name : result.env_b.name}`}
                              style={{ width: '100%', aspectRatio: '16 / 10', objectFit: 'cover', objectPosition: 'top', borderRadius: 6, border: '1px solid var(--border)' }} />
                          </a>
                        ))}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                      <button style={{ ...quietButton, fontSize: 11, padding: '3px 8px' }} disabled={drafting !== null} onClick={() => draft(s.n, 'a')}
                        title={`${result.env_a.name} is right: draft a bug on ${result.env_b.name}`}>Bug on {result.env_b.name}</button>
                      <button style={{ ...quietButton, fontSize: 11, padding: '3px 8px' }} disabled={drafting !== null} onClick={() => draft(s.n, 'b')}
                        title={`${result.env_b.name} is right: draft a bug on ${result.env_a.name}`}>Bug on {result.env_a.name}</button>
                    </div>
                  </>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  )
}
