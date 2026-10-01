/** Session screen panel for the recorded browser: open it on the app under
 *  test, take screenshots, watch what the page reports, stop it. */
import { useCallback, useEffect, useState } from 'react'
import { api, Evidence, RunnerStatus } from '../api/client'
import { RecordingFiles, SIGNAL_KINDS, SignalList, countLine, shortUrl } from './Evidence'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

const POLL_MS = 3000

/** Runner status and everything it recorded, refreshed while the browser runs. */
export function useRunner(sessionId: string | undefined) {
  const [status, setStatus] = useState<RunnerStatus | null>(null)
  const [evidence, setEvidence] = useState<Evidence[]>([])

  const refresh = useCallback(async () => {
    if (!sessionId) return
    const [s, e] = await Promise.all([api.runnerStatus(sessionId), api.listEvidence(sessionId)])
    setStatus(s)
    setEvidence(e)
  }, [sessionId])

  useEffect(() => { refresh().catch(console.error) }, [refresh])
  useEffect(() => {
    if (!status?.running) return
    const id = setInterval(() => refresh().catch(console.error), POLL_MS)
    return () => clearInterval(id)
  }, [status?.running, refresh])

  return { status, evidence, refresh, setStatus }
}

type Runner = ReturnType<typeof useRunner>

export default function BrowserPanel({ sessionId, runner, disabled }: { sessionId: string; runner: Runner; disabled?: boolean }) {
  const { status, evidence, refresh, setStatus } = runner
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState<null | 'start' | 'shot' | 'stop'>(null)
  const [error, setError] = useState<string | null>(null)

  // Prefill with the last address used in this session.
  useEffect(() => { if (status?.start_url) setUrl((current) => current || status.start_url!) }, [status?.start_url])

  async function act(kind: 'start' | 'shot' | 'stop', fn: () => Promise<unknown>) {
    setBusy(kind)
    setError(null)
    try {
      await fn()
      await refresh()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(null)
    }
  }

  const signals = evidence.filter((e) => SIGNAL_KINDS.includes(e.kind)).reverse()
  const pages = evidence.filter((e) => e.kind === 'navigation').reverse().slice(0, 8)
  const running = !!status?.running

  return (
    <aside aria-label="Browser" style={{
      width: 360, flexShrink: 0, background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 11,
      padding: '16px 18px', alignSelf: 'flex-start', position: 'sticky', top: 22,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span aria-hidden style={{
          width: 8, height: 8, borderRadius: '50%',
          background: running ? 'var(--danger)' : 'var(--border)',
          boxShadow: running ? '0 0 0 4px var(--danger-bg)' : 'none',
        }} />
        <h2 style={{ fontSize: 13.5, fontWeight: 600, margin: 0 }}>{running ? 'Recording browser' : 'Recorded browser'}</h2>
      </div>

      {!running && (
        <>
          <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '0 0 12px', lineHeight: 1.5 }}>
            Opens a Chromium window on the app you are testing. Test in it as usual; errors, failed requests,
            pages, a trace and a network log are recorded, and bugs you log get a screenshot.
          </p>
          <form onSubmit={(e) => { e.preventDefault(); act('start', () => api.startRunner(sessionId, url).then(setStatus)) }}>
            <label style={fieldLabel} htmlFor="runner-url">App URL</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input id="runner-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://staging.example.com"
                style={input} disabled={disabled} />
              <button type="submit" disabled={disabled || !url.trim() || !!busy} style={{ ...primaryButton(disabled || !url.trim() || !!busy), whiteSpace: 'nowrap' }}>
                {busy === 'start' ? 'Opening…' : 'Open browser'}
              </button>
            </div>
          </form>
        </>
      )}

      {running && status && (
        <>
          <div style={{ fontSize: 12.5, margin: '6px 0 2px', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {status.window_open ? status.title || 'Untitled page' : 'Browser window closed'}
          </div>
          <div className="mono" title={status.url ?? undefined} style={{ fontSize: 11, color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {status.window_open ? shortUrl(status.url) : 'Stop the browser to save the trace, then open it again.'}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <button onClick={() => act('shot', () => api.takeScreenshot(sessionId))} disabled={!!busy || !status.window_open}
              style={primaryButton(!!busy || !status.window_open)}>
              {busy === 'shot' ? 'Capturing…' : 'Screenshot'}
            </button>
            <button onClick={() => act('stop', () => api.stopRunner(sessionId))} disabled={!!busy} style={quietButton}>
              {busy === 'stop' ? 'Saving trace…' : 'Stop browser'}
            </button>
          </div>
        </>
      )}
      <ErrorLine message={error} />

      {status && countLine(status.counts, [...SIGNAL_KINDS, 'navigation', 'screenshot']) && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', margin: '14px 0 0' }}>
          {countLine(status.counts, [...SIGNAL_KINDS, 'navigation', 'screenshot'])}
        </div>
      )}
      {status && status.dropped > 0 && (
        <div style={{ fontSize: 11.5, color: 'var(--warning)', marginTop: 4 }}>
          {status.dropped} signals were not recorded: the page produced too many at once.
        </div>
      )}

      {(running || signals.length > 0) && (
        <section aria-label="What the page reported" style={{ marginTop: 14 }}>
          <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginBottom: 4 }}>
            What the page reported <span style={{ color: 'var(--text-quiet)' }}>(evidence, not verdicts: you decide)</span>
          </div>
          <div style={{ maxHeight: 240, overflowY: 'auto' }}>
            <SignalList items={signals} empty="Nothing yet: no console errors or failed requests." />
          </div>
        </section>
      )}

      {pages.length > 0 && (
        <section aria-label="Pages visited" style={{ marginTop: 14 }}>
          <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginBottom: 4 }}>Pages visited</div>
          <SignalList items={pages} />
        </section>
      )}

      {!running && (
        <div style={{ marginTop: 14 }}><RecordingFiles items={evidence} /></div>
      )}
    </aside>
  )
}
