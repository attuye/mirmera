/** Regression test for a reviewed bug: generate it from the recorded actions,
 *  read the plan and code, replay it to see whether it catches the bug. */
import { useEffect, useState } from 'react'
import { api, BugListItem, projectUrls, regressionDownloadUrl, RegressionTest, ReplayResult } from '../api/client'
import { EnvironmentChoice } from './BrowserPanel'
import { useProject } from '../project'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

const OUTCOME: Record<ReplayResult['outcome'], { label: string; fg: string; bg: string; hint: string }> = {
  reproduces: {
    label: 'Reproduces the bug', fg: 'var(--accent)', bg: 'var(--accent-bg)',
    hint: 'Every step ran and an assertion failed: the test catches the bug. It will pass once the bug is fixed.',
  },
  passes: {
    label: 'Did not reproduce', fg: 'var(--warning)', bg: 'var(--warning-bg)',
    hint: 'Every step ran and every assertion passed. Either the bug is fixed, it needs other conditions, or the assertions miss it.',
  },
  broken: {
    label: 'Broken', fg: 'var(--danger)', bg: 'var(--danger-bg)',
    hint: 'A step could not run, so the assertions were not checked. The page may have changed, or a locator is ambiguous.',
  },
  incomplete: {
    label: 'Incomplete', fg: 'var(--warning)', bg: 'var(--warning-bg)',
    hint: 'Some steps were skipped (a secret or a file the replay does not have). Assertion results may not mean much.',
  },
  misses_bug: {
    label: "Doesn't reach the bug", fg: 'var(--danger)', bg: 'var(--danger-bg)',
    hint: 'Every step ran, but the request the bug was recorded on was never sent, so the assertions prove nothing. '
      + 'Fix the steps under Steps to the bug (add what is missing), then generate the test again.',
  },
}

const STATUS_COLOR: Record<string, string> = {
  ok: 'var(--accent)', passed: 'var(--accent)', failed: 'var(--danger)', skipped: 'var(--warning)', not_run: 'var(--text-quiet)',
}

const KIND_LABEL: Record<string, string> = {
  visible: 'is visible', hidden: 'is hidden', text: 'shows text', value: 'has value', url: 'URL contains',
  no_console_errors: 'no console errors', no_failed_requests: 'no failed requests',
}

function Replay({ result }: { result: ReplayResult }) {
  const o = OUTCOME[result.outcome]
  const row = (status: string, text: string, detail: string, key: string) => (
    <li key={key} style={{ listStyle: 'none', fontSize: 12.5, padding: '3px 0', display: 'flex', gap: 8 }}>
      <span className="mono" style={{ color: STATUS_COLOR[status], width: 58, flexShrink: 0, fontSize: 11.5 }}>{status.replace('_', ' ')}</span>
      <span style={{ minWidth: 0 }}>
        {text}
        {detail && <span style={{ display: 'block', color: 'var(--text-tertiary)', fontSize: 11.5, wordBreak: 'break-word' }}>{detail}</span>}
      </span>
    </li>
  )
  return (
    <div role="status" aria-label="Replay result" style={{ marginTop: 10 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: o.fg, background: o.bg, padding: '3px 9px', borderRadius: 6 }}>{o.label}</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
          against {result.base_url} in {(result.duration_ms / 1000).toFixed(1)} s
        </span>
      </div>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '6px 0' }}>{o.hint}</div>
      {result.reach && (
        <div className="mono" style={{ fontSize: 11.5, color: result.reach.reached ? 'var(--text-tertiary)' : 'var(--danger)', margin: '0 0 6px' }}>
          {result.reach.reached ? 'Reached' : 'Never sent'} a request to {result.reach.url_contains}
          {result.reach.recorded ? ` (recorded: ${result.reach.recorded})` : ''}
        </div>
      )}
      <ul style={{ margin: 0, padding: 0 }}>
        {result.steps.map((s) => row(s.status, `${s.n}. ${s.description}`, s.detail, `s${s.n}`))}
        {result.assertions.map((a) => row(a.status, `Check ${a.n}: ${KIND_LABEL[a.kind] ?? a.kind}. ${a.why}`,
          a.status === 'failed' ? a.detail : '', `a${a.n}`))}
      </ul>
    </div>
  )
}

export default function RegressionTestPanel({ bug }: { bug: BugListItem }) {
  const [test, setTest] = useState<RegressionTest | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [knownFailing, setKnownFailing] = useState(true)
  const { project } = useProject()
  const environments = projectUrls(project)
  const [baseUrl, setBaseUrl] = useState('')
  const [showCode, setShowCode] = useState(true)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState<null | 'generate' | 'replay'>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getRegressionTest(bug.id).then((t) => { setTest(t); setLoaded(true) }).catch((e) => { setError(errorText(e)); setLoaded(true) })
  }, [bug.id])
  useEffect(() => { if (test?.base_url) setBaseUrl((u) => u || test.base_url!) }, [test?.base_url])

  async function run(kind: 'generate' | 'replay') {
    setBusy(kind)
    setError(null)
    try {
      setTest(kind === 'generate'
        ? await api.generateRegressionTest(bug.id, knownFailing)
        : await api.replayRegressionTest(test!.id, baseUrl.trim() || undefined))
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(null)
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(test!.code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* clipboard blocked: the code is selectable */ }
  }

  if (!loaded) return null

  return (
    <section aria-label="Regression test" style={{
      marginTop: 14, border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', background: 'var(--panel)',
    }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap', marginBottom: 8 }}>
        <h3 style={{ fontSize: 13.5, fontWeight: 600, margin: 0 }}>Regression test</h3>
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
          Playwright test built from your recorded actions; it asserts what should happen (the oracle).
        </span>
      </div>

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <button onClick={() => run('generate')} disabled={!!busy} style={test ? quietButton : primaryButton(!!busy)}>
          {busy === 'generate' ? 'Generating…' : test ? 'Generate again' : 'Generate regression test'}
        </button>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={knownFailing} onChange={(e) => setKnownFailing(e.target.checked)} />
          Mark as a known bug (<span className="mono">test.fail()</span>): CI stays green until the fix, then tells you
        </label>
      </div>

      {test && (
        <>
          {test.warnings.length > 0 && test.warnings.map((w, i) => (
            <div key={i} style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 8 }}>{w}</div>
          ))}
          {test.plan.assumptions.length > 0 && (
            <div aria-label="Assumptions" style={{ fontSize: 12.5, color: 'var(--warning)', marginTop: 10 }}>
              Check before you trust it:
              <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{test.plan.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
            <span className="mono" style={{ fontSize: 12 }}>{test.filename}</span>
            <button onClick={() => setShowCode(!showCode)} aria-expanded={showCode} style={{ ...quietButton, padding: '3px 9px', fontSize: 11.5 }}>
              {showCode ? 'Hide code' : 'Show code'}
            </button>
            <button onClick={copy} style={{ ...quietButton, padding: '3px 9px', fontSize: 11.5 }}>{copied ? 'Copied' : 'Copy'}</button>
            <a href={regressionDownloadUrl(test.id)} download style={{ fontSize: 12 }}>Download</a>
            <span style={{ fontSize: 11.5, color: 'var(--text-quiet)' }}>by {test.ai_provider}/{test.ai_model}</span>
          </div>
          {showCode && (
            <pre aria-label="Test code" style={{
              margin: '8px 0 0', fontSize: 11.5, lineHeight: 1.55, fontFamily: 'var(--font-mono)', whiteSpace: 'pre',
              background: 'var(--panel-alt)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px',
              maxHeight: 360, overflow: 'auto', color: 'var(--text-secondary)',
            }}>{test.code}</pre>
          )}

          <>
          <form onSubmit={(e) => { e.preventDefault(); run('replay') }}
            style={{ display: 'flex', gap: 8, alignItems: 'flex-end', marginTop: 12, flexWrap: 'wrap' }}>
            <label style={{ flex: 1, minWidth: 240 }}>
              <span style={fieldLabel}>Replay against</span>
              <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} style={input} placeholder="https://staging.example.com" />
            </label>
            <button type="submit" disabled={!!busy} style={primaryButton(!!busy)}>
              {busy === 'replay' ? 'Replaying…' : 'Replay test'}
            </button>
          </form>
          {environments.length > 0 && <EnvironmentChoice environments={environments} current={baseUrl} onPick={setBaseUrl} />}
          </>
          {test.replay && <Replay result={test.replay} />}
        </>
      )}
      <ErrorLine message={error} />
    </section>
  )
}
