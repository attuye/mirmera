/** API exploration in a session: build a request (typed, a pasted curl, or replayed from what
 *  the recorded browser saw), send it from the Mirmera server, read the answer, vary it, and log
 *  a bug or a note from it. Every request is kept as evidence of the session, with secrets blanked. */
import { useEffect, useState } from 'react'
import { api, ApiDraft, ApiHeader, ApiMethod, ApiRequestInput, ApiResponse, Evidence, Note, Project, RecordedRequest } from '../api/client'
import { ErrorLine, errorText, input, quietButton } from './ui'
import { clock } from './Evidence'
import OpenApiPicker from './OpenApiPicker'

const METHODS: ApiMethod[] = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
const AUTH = /^(authorization|cookie|x-api-key|api-key|x-auth-token)$/i
const MASK = '•••'
const small: React.CSSProperties = { ...quietButton, fontSize: 11.5, padding: '4px 9px' }
const field: React.CSSProperties = { ...input, fontSize: 12, padding: '6px 8px' }
const label: React.CSSProperties = { fontSize: 11, color: 'var(--text-tertiary)', margin: '10px 0 4px', display: 'block' }

/** Shared headers for every request in this session (a token, say): kept in this browser tab only. */
function useSessionHeaders(sessionId: string): [ApiHeader[], (h: ApiHeader[]) => void] {
  const key = `mirmera.apiHeaders.${sessionId}`
  const [headers, setHeaders] = useState<ApiHeader[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(key) ?? '[]') } catch { return [] }
  })
  return [headers, (h) => {
    setHeaders(h)
    try { sessionStorage.setItem(key, JSON.stringify(h)) } catch { /* not kept */ }
  }]
}

function statusColor(status: number | null): string {
  if (status === null) return 'var(--danger)'
  if (status >= 500) return 'var(--danger)'
  if (status >= 400) return 'var(--warning)'
  if (status >= 300) return 'var(--info)'
  return 'var(--accent)'
}

function pretty(body: string | null, contentType: string): string {
  if (!body) return ''
  if (contentType.includes('json') || /^\s*[{[]/.test(body)) {
    try { return JSON.stringify(JSON.parse(body), null, 2) } catch { /* not JSON after all */ }
  }
  return body
}

function HeaderRows({ rows, onChange, prefix }: { rows: ApiHeader[]; onChange: (r: ApiHeader[]) => void; prefix: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {rows.map((h, i) => (
        <div key={i} style={{ display: 'flex', gap: 4 }}>
          <input aria-label={`${prefix} header ${i + 1} name`} value={h.name} placeholder="Name" style={{ ...field, width: 140 }}
            onChange={(e) => onChange(rows.map((r, j) => (j === i ? { ...r, name: e.target.value } : r)))} />
          <input aria-label={`${prefix} header ${i + 1} value`} value={h.value} placeholder="Value" style={field}
            type={AUTH.test(h.name) ? 'password' : 'text'} autoComplete="off"
            onChange={(e) => onChange(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))} />
          <button type="button" aria-label={`Remove ${prefix.toLowerCase()} header ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))} style={small}>×</button>
        </div>
      ))}
      <button type="button" onClick={() => onChange([...rows, { name: '', value: '' }])} style={{ ...small, alignSelf: 'flex-start' }}>+ Header</button>
    </div>
  )
}

export default function ApiPanel({ sessionId, project, disabled, onNote }: {
  sessionId: string; project: Project; disabled: boolean; onNote: (n: Note) => void
}) {
  const [req, setReq] = useState<ApiDraft>({ method: 'GET', url: '', headers: [], body: null })
  const [source, setSource] = useState<ApiRequestInput['source']>('manual')
  const [sessionHeaders, setSessionHeaders] = useSessionHeaders(sessionId)
  const [showShared, setShowShared] = useState(sessionHeaders.length > 0)
  const [importing, setImporting] = useState<'curl' | 'recorded' | 'openapi' | null>(null)
  const [curl, setCurl] = useState('')
  const [recorded, setRecorded] = useState<RecordedRequest[]>([])
  const [history, setHistory] = useState<Evidence[]>([])
  const [last, setLast] = useState<{ response: ApiResponse; evidence: Evidence; sent: ApiDraft } | null>(null)
  const [view, setView] = useState<'body' | 'headers'>('body')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.apiHistory(sessionId).then(setHistory).catch(() => {}) }, [sessionId])
  const hasBody = !['GET', 'HEAD'].includes(req.method)
  const masked = req.headers.some((h) => h.value === MASK)

  function load(d: ApiDraft, from: ApiRequestInput['source'], message?: string) {
    setReq({ ...d, headers: d.headers.filter((h) => h.value !== MASK), body: d.body ?? null })
    setSource(from); setImporting(null); setNotice(message ?? null); setError(null)
  }

  async function send(draft: ApiDraft, from: ApiRequestInput['source'], withShared = true) {
    setBusy(true); setError(null); setNotice(null)
    const own = new Set(draft.headers.map((h) => h.name.toLowerCase()))
    const shared = withShared ? sessionHeaders.filter((h) => h.name.trim() && !own.has(h.name.toLowerCase())) : []
    const headers = [...shared, ...draft.headers].filter((h) => h.name.trim())
    try {
      const out = await api.sendApiRequest(sessionId, { ...draft, headers, body: ['GET', 'HEAD'].includes(draft.method) ? null : draft.body, source: from })
      setLast({ ...out, sent: draft }); setView('body')
      setHistory((h) => [out.evidence, ...h])
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  async function logBug() {
    if (!last) return
    const r = last.response
    const title = `${last.sent.method} ${new URL(r.url).pathname} answered ${r.status ?? r.error}`
    try {
      await api.logBug(sessionId, { title, severity: 'medium', evidence_ids: [last.evidence.id] })
      onNote(await api.addNote(sessionId, 'bug', title))
      setNotice('Bug logged with this request attached. Review it on the Bugs page.')
    } catch (e) { setError(errorText(e)) }
  }

  async function noteIt() {
    if (!last) return
    try {
      onNote(await api.addNote(sessionId, 'test', `Sent ${last.evidence.summary}`))
      setNotice('Noted.')
    } catch (e) { setError(errorText(e)) }
  }

  const r = last?.response
  return (
    <section aria-label="API" style={{ width: 460, flexShrink: 0, background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 11, padding: '12px 14px', alignSelf: 'flex-start' }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>Start from:</span>
        <button type="button" onClick={() => setImporting(importing === 'curl' ? null : 'curl')} aria-pressed={importing === 'curl'} style={small}>Paste curl</button>
        <button type="button" aria-pressed={importing === 'recorded'} style={small} onClick={async () => {
          if (importing === 'recorded') { setImporting(null); return }
          try { setRecorded(await api.recordedRequests(sessionId)); setImporting('recorded') } catch (e) { setError(errorText(e)) }
        }}>From the browser</button>
        <button type="button" onClick={() => setImporting(importing === 'openapi' ? null : 'openapi')} aria-pressed={importing === 'openapi'} style={small}>From OpenAPI</button>
      </div>

      {importing === 'curl' && (
        <div style={{ marginTop: 8 }}>
          <textarea aria-label="curl command" value={curl} onChange={(e) => setCurl(e.target.value)} placeholder={"curl 'https://…' -H 'accept: application/json'\n(DevTools → Network → Copy as cURL)"}
            style={{ ...field, height: 90, fontFamily: 'var(--font-mono)', fontSize: 11.5, resize: 'vertical' }} />
          <button type="button" disabled={!curl.trim()} style={{ ...small, marginTop: 4 }} onClick={async () => {
            try { load(await api.parseCurl(sessionId, curl), 'curl', 'Filled in from the curl command.'); setCurl('') } catch (e) { setError(errorText(e)) }
          }}>Use this command</button>
        </div>
      )}
      {importing === 'openapi' && (
        <div style={{ marginTop: 8 }}>
          <OpenApiPicker project={project} onPick={(d, message) => load(d, 'openapi', message)} />
        </div>
      )}
      {importing === 'recorded' && (
        <div role="list" aria-label="Recorded requests" style={{ marginTop: 8, maxHeight: 180, overflowY: 'auto', border: '1px solid var(--border-soft)', borderRadius: 7 }}>
          {recorded.length === 0 && <div style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: 8 }}>The browser hasn't recorded a failed or slow request in this session yet.</div>}
          {recorded.map((x, i) => (
            <button key={i} role="listitem" type="button" onClick={() => load(x, 'recorded', 'Loaded what the browser sent; credentials weren’t kept, so add them if the API needs them.')}
              style={{ display: 'block', width: '100%', textAlign: 'left', background: 'transparent', border: 'none', borderBottom: '1px solid var(--border-soft)', padding: '6px 8px', cursor: 'pointer', color: 'var(--text)' }}>
              <span className="mono" style={{ fontSize: 11, color: x.kind === 'failed_request' ? 'var(--warning)' : 'var(--info)' }}>{x.kind === 'failed_request' ? 'FAILED' : 'SLOW'}{x.count > 1 ? ` ×${x.count}` : ''}</span>{' '}
              <span style={{ fontSize: 12 }}>{x.seen}</span>
            </button>
          ))}
        </div>
      )}

      <form aria-label="Request" onSubmit={(e) => { e.preventDefault(); if (req.url.trim()) send(req, source) }}>
        <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
          <select aria-label="Method" value={req.method} onChange={(e) => setReq({ ...req, method: e.target.value as ApiMethod })} style={{ ...field, width: 96 }}>
            {METHODS.map((m) => <option key={m}>{m}</option>)}
          </select>
          <input aria-label="URL" value={req.url} onChange={(e) => { setReq({ ...req, url: e.target.value }); setSource(source === 'manual' ? 'manual' : 'variation') }}
            placeholder={project.app_url ? '/api/… or a full address' : 'https://…'} style={{ ...field, fontFamily: 'var(--font-mono)' }} />
        </div>
        {project.app_url && req.url.startsWith('/') && <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 3 }}>On {project.app_url}</div>}

        <span style={label}>Headers</span>
        <HeaderRows rows={req.headers} onChange={(headers) => setReq({ ...req, headers })} prefix="Request" />
        {masked && <div style={{ fontSize: 11, color: 'var(--warning)' }}>Some values weren't kept (•••): type them again.</div>}

        <button type="button" onClick={() => setShowShared(!showShared)} style={{ ...small, marginTop: 8 }} aria-expanded={showShared}>
          Session headers{sessionHeaders.length ? ` (${sessionHeaders.length})` : ''}
        </button>
        {showShared && (
          <div style={{ marginTop: 6, padding: 8, border: '1px dashed var(--border)', borderRadius: 7 }}>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 6 }}>Sent with every request in this session (a token, say). Kept in this browser tab only, never saved.</div>
            <HeaderRows rows={sessionHeaders} onChange={setSessionHeaders} prefix="Session" />
          </div>
        )}

        {hasBody && (
          <>
            <span style={label}>Body</span>
            <textarea aria-label="Body" value={req.body ?? ''} onChange={(e) => setReq({ ...req, body: e.target.value })}
              style={{ ...field, height: 100, fontFamily: 'var(--font-mono)', fontSize: 11.5, resize: 'vertical' }} placeholder='{"type": "passport"}' />
          </>
        )}
        <button type="submit" disabled={disabled || busy || !req.url.trim()} style={{
          marginTop: 10, width: '100%', padding: '8px 0', borderRadius: 7, fontSize: 12.5, fontWeight: 600,
          background: disabled || busy || !req.url.trim() ? 'var(--border)' : 'var(--accent)', color: '#0C0D0F',
        }}>{busy ? 'Sending…' : 'Send'}</button>
      </form>
      <ErrorLine message={error} />
      {notice && <div role="status" style={{ fontSize: 11.5, color: 'var(--accent)', marginTop: 6 }}>{notice}</div>}

      {r && (
        <div aria-label="Response" role="region" style={{ marginTop: 12, borderTop: '1px solid var(--border-soft)', paddingTop: 10 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: statusColor(r.status) }}>{r.status !== null ? `${r.status} ${r.reason ?? ''}` : 'No answer'}</span>
            <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{r.ms} ms · {r.size.toLocaleString()} bytes</span>
            <span style={{ marginLeft: 'auto', display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
              {(['body', 'headers'] as const).map((v) => (
                <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)} style={{
                  border: 'none', fontSize: 11, padding: '3px 8px', cursor: 'pointer',
                  background: view === v ? 'var(--accent-bg)' : 'transparent', color: view === v ? 'var(--accent)' : 'var(--text-secondary)',
                }}>{v === 'body' ? 'Body' : `Headers (${r.headers.length})`}</button>
              ))}
            </span>
          </div>
          {r.error && <div role="alert" style={{ fontSize: 12, color: 'var(--danger)', marginTop: 6 }}>{r.error}</div>}
          <pre aria-label="Response body" style={{
            marginTop: 8, maxHeight: 260, overflow: 'auto', background: 'var(--bg)', border: '1px solid var(--border-soft)', borderRadius: 7,
            padding: 8, fontSize: 11.5, fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          }}>{view === 'body' ? (pretty(r.body, r.content_type) || '(empty)') : r.headers.map((h) => `${h.name}: ${h.value}`).join('\n')}</pre>
          {r.truncated && <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Only the first 2 MB were read.</div>}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
            <button type="button" onClick={logBug} style={{ ...small, color: 'var(--danger)' }}>Log as bug</button>
            <button type="button" onClick={noteIt} style={small}>Note it</button>
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)', alignSelf: 'center', marginLeft: 4 }}>Vary:</span>
            <button type="button" disabled={busy} onClick={() => send(last!.sent, 'variation')} style={small}>Send again</button>
            <button type="button" disabled={busy} style={small} onClick={() =>
              send({ ...last!.sent, headers: last!.sent.headers.filter((h) => !AUTH.test(h.name)) }, 'variation', false)}>Without auth</button>
            {!['GET', 'HEAD'].includes(last!.sent.method) && (
              <button type="button" disabled={busy} onClick={() => send({ ...last!.sent, body: '' }, 'variation')} style={small}>Empty body</button>
            )}
          </div>
        </div>
      )}

      {history.length > 0 && (
        <div style={{ marginTop: 12, borderTop: '1px solid var(--border-soft)', paddingTop: 8 }}>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 4 }}>Sent in this session</div>
          <ul aria-label="Sent requests" style={{ listStyle: 'none', margin: 0, padding: 0, maxHeight: 160, overflowY: 'auto' }}>
            {history.slice(0, 30).map((h) => (
              <li key={h.id}>
                <button type="button" title="Load into the request" onClick={() => h.data && load({
                  method: h.data.method, url: h.url ?? '', headers: h.data.request_headers, body: h.data.request_body,
                }, 'variation', 'Loaded from history; credentials weren’t kept, so add them again if needed.')}
                  style={{ display: 'flex', gap: 8, width: '100%', textAlign: 'left', background: 'transparent', border: 'none', padding: '3px 0', cursor: 'pointer', color: 'var(--text-secondary)', fontSize: 11.5 }}>
                  <span className="mono" style={{ color: 'var(--text-quiet)', flexShrink: 0 }}>{clock(h.captured_at)}</span>
                  <span className="mono" style={{ color: statusColor(h.data?.status ?? null), flexShrink: 0 }}>{h.data?.status ?? '—'}</span>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.data?.method} {h.url}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
