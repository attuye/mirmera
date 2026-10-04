/** Retest when a ticket closes: look up every issue filed from the project and,
 *  for each one closed since the last look, replay the bug's test and comment
 *  on the issue with the result. */
import { useCallback, useEffect, useState } from 'react'
import { api, Retest, RetestCheck } from '../api/client'
import { utcDate } from './Evidence'
import { ErrorLine, errorText, primaryButton } from './ui'

const POLL_MS = 1500
const TRACKER = { github: 'GitHub', jira: 'Jira' } as const

const OUTCOME: Record<Retest['outcome'], { label: string; fg: string; bg: string }> = {
  running: { label: 'Replaying…', fg: 'var(--text-secondary)', bg: 'var(--panel-alt)' },
  fixed: { label: 'Fix verified', fg: 'var(--accent)', bg: 'var(--accent-bg)' },
  still_fails: { label: 'Still fails', fg: 'var(--danger)', bg: 'var(--danger-bg)' },
  could_not_check: { label: "Couldn't check", fg: 'var(--warning)', bg: 'var(--warning-bg)' },
  no_test: { label: 'No test', fg: 'var(--text-secondary)', bg: 'var(--panel-alt)' },
}

function what(r: Retest): string {
  if (r.outcome === 'running') return 'Replaying the regression test'
  if (r.outcome === 'fixed') return 'The regression test passes'
  if (r.outcome === 'still_fails') {
    const failed = r.replay?.assertions.filter((a) => a.status === 'failed').map((a) => a.why || a.kind) ?? []
    return `The bug is still there${failed.length ? `: ${failed.join('; ')}` : ''}. The issue may need reopening`
  }
  return r.detail ?? ''
}

function comment(r: Retest): string {
  if (r.outcome === 'running' || r.outcome === 'no_test') return ''
  if (r.commented) return `Result commented on ${r.issue_key}`
  return r.comment_error ? `Comment not posted: ${r.comment_error}` : ''
}

export default function ClosedIssues({ projectId, baseUrl }: { projectId: string; baseUrl: string }) {
  const [retests, setRetests] = useState<Retest[]>([])
  const [last, setLast] = useState<RetestCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => api.listRetests(projectId).then(setRetests), [projectId])
  useEffect(() => { load().catch((e) => setError(errorText(e))) }, [load])

  const running = retests.some((r) => r.outcome === 'running')
  useEffect(() => {
    if (!running) return
    const t = window.setInterval(() => { load().catch(() => {}) }, POLL_MS)
    return () => window.clearInterval(t)
  }, [running, load])

  async function check() {
    setBusy(true); setError(null)
    try {
      setLast(await api.checkClosedIssues(projectId, baseUrl.trim() || undefined))
      await load()
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  return (
    <section aria-label="Closed issues" style={{ marginTop: 30 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
        <div>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Retest closed issues</h2>
          <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 640, lineHeight: 1.5 }}>
            Looks up every issue filed from this project. When one was closed since the last check, Mirmera replays the
            bug's regression test and comments on the issue: fix verified, still fails, or couldn't check.
          </p>
        </div>
        <button onClick={check} disabled={busy} style={{ ...primaryButton(busy), whiteSpace: 'nowrap' }}>
          {busy ? 'Checking…' : 'Check closed issues'}
        </button>
      </div>
      <ErrorLine message={error} />
      {last && (
        <div role="status" style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 8 }}>
          Checked {last.checked} of {last.filed} filed issue{last.filed === 1 ? '' : 's'}:{' '}
          {last.retests.length === 0 ? 'none newly closed.' : `${last.retests.length} newly closed, retesting.`}
          {last.problems.map((p) => <div key={p} style={{ color: 'var(--warning)' }}>{p}</div>)}
        </div>
      )}
      {retests.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 10, fontSize: 12.5 }}>
          <tbody>
            {retests.map((r) => {
              const o = OUTCOME[r.outcome]
              return (
                <tr key={r.id} aria-label={`Retest ${r.issue_key}`} style={{ borderTop: '1px solid var(--border-soft)' }}>
                  <td style={{ padding: '6px', width: 130 }}>
                    <span style={{ fontSize: 11, fontWeight: 600, color: o.fg, background: o.bg, padding: '2px 7px', borderRadius: 5, whiteSpace: 'nowrap' }}>{o.label}</span>
                  </td>
                  <td style={{ padding: '6px' }}>
                    <div>
                      <a href={r.issue_url} target="_blank" rel="noreferrer" className="mono" style={{ fontSize: 11.5 }}>{TRACKER[r.tracker]} {r.issue_key} ↗</a>{' '}
                      {r.bug_title}
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>
                      {what(r)}{comment(r) ? ` · ${comment(r)}` : ''}
                    </div>
                  </td>
                  <td className="mono" style={{ padding: '6px', fontSize: 11, color: 'var(--text-quiet)', textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {utcDate(r.created_at).toLocaleString()}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}
