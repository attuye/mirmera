/** Project settings: which AI does which task, and a check of how a model copes
 *  with each task (usable first time, needed a retry, or failed). */
import { useEffect, useState } from 'react'
import { AICheckResult, AIOverview, AI_PROVIDERS, api } from '../api/client'
import { errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

const RESULT: Record<AICheckResult['result'], { label: string; fg: string; bg: string }> = {
  ok: { label: 'OK', fg: 'var(--accent)', bg: 'var(--accent-bg)' },
  retried: { label: 'Needed a retry', fg: 'var(--warning)', bg: 'var(--warning-bg)' },
  failed: { label: 'Failed', fg: 'var(--danger)', bg: 'var(--danger-bg)' },
}

export default function AISettings({ projectId, defaultProvider, choices, onDefault, onChoices }: {
  projectId?: string  // check providers with this project's workspace keys
  defaultProvider: string
  choices: Record<string, string>
  onDefault: (p: string) => void
  onChoices: (c: Record<string, string>) => void
}) {
  const [overview, setOverview] = useState<AIOverview | null>(null)
  const [checking, setChecking] = useState<string>(defaultProvider)
  const [results, setResults] = useState<Record<string, AICheckResult>>({})
  const [running, setRunning] = useState<string | null>(null)  // task being checked
  const [error, setError] = useState<string | null>(null)
  const small = { ...input, padding: '5px 8px', fontSize: 12.5 }

  useEffect(() => { api.aiOverview(projectId).then(setOverview).catch((e) => setError(errorText(e))) }, [projectId])
  if (!overview) return error ? <div role="alert" style={{ color: 'var(--danger)', fontSize: 12.5 }}>{error}</div> : null
  const status = Object.fromEntries(overview.providers.map((p) => [p.name, p]))

  async function runCheck() {
    setResults({}); setError(null)
    for (const t of overview!.tasks) {
      setRunning(t.key)
      try {
        const r = await api.aiCheck(checking, t.key, projectId)
        setResults((prev) => ({ ...prev, [t.key]: r }))
      } catch (e) { setError(errorText(e)); break }
    }
    setRunning(null)
  }
  const checked = Object.values(results)
  const firstTime = overview.tasks.filter((t) => results[t.key]?.result === 'ok').map((t) => t.key)
  const done = checked.length === overview.tasks.length && !running

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div>
        <label htmlFor="s-provider" style={fieldLabel}>Default provider</label>
        <select id="s-provider" value={defaultProvider} onChange={(e) => onDefault(e.target.value)} style={{ ...input, width: 200 }}>
          {AI_PROVIDERS.map((p) => <option key={p} value={p}>{p}{status[p] && !status[p].configured ? ' (not set up)' : ''}</option>)}
        </select>
        {status[defaultProvider] && !status[defaultProvider].configured && (
          <div style={{ fontSize: 11.5, color: 'var(--warning)', marginTop: 5 }}>{status[defaultProvider].problem}</div>
        )}
      </div>

      <table aria-label="Provider per task" style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
        <thead>
          <tr style={{ color: 'var(--text-tertiary)', textAlign: 'left' }}>
            <th style={{ fontWeight: 500, padding: '4px 6px' }}>Task</th>
            <th style={{ fontWeight: 500, padding: '4px 6px', width: 170 }}>Provider</th>
            <th style={{ fontWeight: 500, padding: '4px 6px' }}>Check{checked.length ? ` (${checking})` : ''}</th>
          </tr>
        </thead>
        <tbody>
          {overview.tasks.map((t) => {
            const r = results[t.key]
            return (
              <tr key={t.key} aria-label={t.label} style={{ borderTop: '1px solid var(--border-soft)' }}>
                <td style={{ padding: '6px' }}>
                  <div>{t.label}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-quiet)' }}>{t.hint}</div>
                </td>
                <td style={{ padding: '6px' }}>
                  <select aria-label={`Provider for ${t.label}`} value={choices[t.key] ?? ''} style={small}
                    onChange={(e) => {
                      const next = { ...choices }
                      if (e.target.value) next[t.key] = e.target.value; else delete next[t.key]
                      onChoices(next)
                    }}>
                    <option value="">Default ({defaultProvider})</option>
                    {AI_PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                </td>
                <td style={{ padding: '6px' }} title={r?.detail ?? r?.sample ?? undefined}>
                  {running === t.key && <span style={{ color: 'var(--text-tertiary)' }}>Checking…</span>}
                  {r && (
                    <span style={{ display: 'flex', gap: 6, alignItems: 'baseline', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 11, fontWeight: 600, color: RESULT[r.result].fg, background: RESULT[r.result].bg, padding: '1px 6px', borderRadius: 5 }}>
                        {RESULT[r.result].label}
                      </span>
                      <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{r.seconds}s</span>
                      <span style={{ fontSize: 11, color: 'var(--text-quiet)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 220 }}>
                        {r.result === 'failed' ? r.detail : r.sample}
                      </span>
                    </span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <div style={{ border: '1px solid var(--border)', borderRadius: 9, padding: '12px 14px', background: 'var(--panel-alt)' }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>Check a model</div>
        <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '4px 0 10px', lineHeight: 1.5 }}>
          Runs every task once with made-up practice data and shows whether the reply was usable first time, needed a
          retry, or failed, and how long it took. Nothing is saved. A local model can take a few minutes.
        </p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select aria-label="Model to check" value={checking} onChange={(e) => setChecking(e.target.value)} disabled={!!running} style={{ ...small, width: 160 }}>
            {AI_PROVIDERS.map((p) => <option key={p} value={p}>{p}{status[p]?.model ? ` (${status[p].model})` : ''}</option>)}
          </select>
          <button type="button" onClick={runCheck} disabled={!!running} style={primaryButton(!!running)}>
            {running ? `Checking ${checked.length + 1} of ${overview.tasks.length}…` : 'Check this model'}
          </button>
          {done && firstTime.length > 0 && (
            <button type="button" style={quietButton}
              onClick={() => onChoices({ ...choices, ...Object.fromEntries(firstTime.map((k) => [k, checking])) })}>
              Use {checking} for the {firstTime.length} task{firstTime.length === 1 ? '' : 's'} it handled first time
            </button>
          )}
        </div>
        {done && (
          <div role="status" style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 8 }}>
            {checking}: {firstTime.length} OK, {checked.filter((r) => r.result === 'retried').length} needed a retry,{' '}
            {checked.filter((r) => r.result === 'failed').length} failed, {checked.reduce((s, r) => s + r.seconds, 0).toFixed(0)}s in all.
          </div>
        )}
        {error && <div role="alert" style={{ color: 'var(--danger)', fontSize: 12.5, marginTop: 8 }}>{error}</div>}
      </div>
    </div>
  )
}
