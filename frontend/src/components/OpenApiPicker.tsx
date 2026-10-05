/** Pick an endpoint from the project's OpenAPI spec for the API tab: each comes with a ready
 *  request (example body, required parameters) and shows whether the project's sessions tried it.
 *  With no spec yet, import one from an address or by pasting it. */
import { useEffect, useMemo, useState } from 'react'
import { api, ApiDraft, OpenApiEndpoint, OpenApiOperations, OpenApiSpec, Project } from '../api/client'
import { ConfirmDelete, ErrorLine, errorText, input, quietButton } from './ui'

const small: React.CSSProperties = { ...quietButton, fontSize: 11.5, padding: '4px 9px' }
const field: React.CSSProperties = { ...input, fontSize: 12, padding: '6px 8px' }
const METHOD_COLOR: Record<string, string> = { GET: 'var(--info)', POST: 'var(--accent)', PUT: 'var(--warning)', PATCH: 'var(--warning)', DELETE: 'var(--danger)' }
const RELATIVE = ''  // "send to the project's app URL"

function Importer({ project, onDone, onCancel }: { project: Project; onDone: (s: OpenApiSpec) => void; onCancel?: () => void }) {
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function run() {
    setBusy(true); setError(null)
    try { onDone(await api.importOpenApi(project.id, url.trim() ? { url: url.trim() } : { text })) } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return (
    <div aria-label="Import OpenAPI" role="group" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>Import the API's OpenAPI or Swagger file: its address, or paste it (JSON or YAML).</div>
      <input aria-label="Spec address" value={url} onChange={(e) => { setUrl(e.target.value); if (e.target.value) setText('') }}
        placeholder={project.app_url ? `${project.app_url.replace(/\/$/, '')}/openapi.json` : 'https://…/openapi.json'} style={{ ...field, fontFamily: 'var(--font-mono)' }} />
      <textarea aria-label="Spec text" value={text} onChange={(e) => { setText(e.target.value); if (e.target.value) setUrl('') }}
        placeholder="…or paste the spec here" style={{ ...field, height: 70, fontFamily: 'var(--font-mono)', fontSize: 11, resize: 'vertical' }} />
      <div style={{ display: 'flex', gap: 6 }}>
        <button type="button" disabled={busy || (!url.trim() && !text.trim())} onClick={run} style={small}>{busy ? 'Importing…' : 'Import'}</button>
        {onCancel && <button type="button" onClick={onCancel} style={small}>Cancel</button>}
      </div>
      <ErrorLine message={error} />
    </div>
  )
}

export default function OpenApiPicker({ project, onPick }: { project: Project; onPick: (d: ApiDraft, notice: string) => void }) {
  const [specs, setSpecs] = useState<OpenApiSpec[] | null>(null)
  const [specId, setSpecId] = useState('')
  const [ops, setOps] = useState<OpenApiOperations | null>(null)
  const [server, setServer] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.openApiSpecs(project.id).then((s) => { setSpecs(s); setSpecId(s[0]?.id ?? '') }).catch((e) => setError(errorText(e)))
  }, [project.id])
  useEffect(() => {
    if (!specId) { setOps(null); return }
    api.openApiEndpoints(project.id, specId).then((o) => {
      setOps(o)
      // Absolute servers from the spec first; a relative one (or none) means the project's app URL.
      const absolute = o.servers.find((s) => /^https?:\/\//.test(s))
      setServer(absolute ?? RELATIVE)
    }).catch((e) => setError(errorText(e)))
  }, [project.id, specId])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (ops?.endpoints ?? []).filter((e) => !q || `${e.key} ${e.summary} ${e.tag}`.toLowerCase().includes(q))
  }, [ops, query])
  const tags = [...new Set(shown.map((e) => e.tag))]

  function pick(e: OpenApiEndpoint) {
    const relativeBase = ops!.servers.find((s) => s.startsWith('/')) ?? ''
    const url = server ? `${server}${e.url}` : `${relativeBase}${e.url}`
    const hints = [
      e.unfilled.length ? `Fill in ${e.unfilled.map((p) => `{${p}}`).join(', ')} in the address.` : '',
      e.auth.length ? `Needs ${e.auth.join(' or ')}: add it under Session headers if you haven't.` : '',
      e.deprecated ? 'This endpoint is deprecated.' : '',
    ].filter(Boolean)
    onPick({ method: e.method, url, headers: e.headers, body: e.body }, [`${e.key} from the spec.`, ...hints].join(' '))
  }

  if (specs === null) return <ErrorLine message={error} />
  if (specs.length === 0 || importing) {
    return <Importer project={project} onCancel={specs.length ? () => setImporting(false) : undefined}
      onDone={(s) => { setSpecs((prev) => [...(prev ?? []).filter((x) => x.id !== s.id), s]); setSpecId(s.id); setImporting(false); setOps(null) }} />
  }
  return (
    <div aria-label="OpenAPI endpoints" role="group" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        {specs.length > 1
          ? <select aria-label="Spec" value={specId} onChange={(e) => setSpecId(e.target.value)} style={{ ...field, width: 'auto' }}>
              {specs.map((s) => <option key={s.id} value={s.id}>{s.title} {s.version}</option>)}
            </select>
          : <span style={{ fontSize: 12, fontWeight: 600 }}>{specs[0].title} <span style={{ fontWeight: 400, color: 'var(--text-tertiary)' }}>{specs[0].version}</span></span>}
        {ops && <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{ops.tried} of {ops.endpoints.length} endpoints tried in this project</span>}
      </div>
      {ops && (
        <label style={{ fontSize: 11.5, color: 'var(--text-tertiary)', display: 'flex', gap: 6, alignItems: 'center' }}>Send to
          <select aria-label="Send to" value={server ?? RELATIVE} onChange={(e) => setServer(e.target.value)} style={{ ...field, width: 'auto', flex: 1 }}>
            {ops.servers.filter((s) => /^https?:\/\//.test(s)).map((s) => <option key={s} value={s}>{s}</option>)}
            <option value={RELATIVE}>The project's app URL{project.app_url ? ` (${project.app_url})` : ''}</option>
          </select>
        </label>
      )}
      <input aria-label="Search endpoints" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search: documents, delete, upload…" style={field} />
      <div role="list" aria-label="Endpoints" style={{ maxHeight: 240, overflowY: 'auto', border: '1px solid var(--border-soft)', borderRadius: 7 }}>
        {tags.map((tag) => (
          <div key={tag}>
            <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: 0.4, padding: '6px 8px 2px' }}>{tag}</div>
            {shown.filter((e) => e.tag === tag).map((e) => (
              <button key={e.key} role="listitem" type="button" aria-label={e.key} onClick={() => pick(e)} title={e.summary} style={{
                display: 'flex', gap: 8, alignItems: 'baseline', width: '100%', textAlign: 'left', background: 'transparent', border: 'none',
                borderTop: '1px solid var(--border-soft)', padding: '5px 8px', cursor: 'pointer', color: 'var(--text)',
              }}>
                <span className="mono" style={{ fontSize: 10.5, width: 50, flexShrink: 0, color: METHOD_COLOR[e.method] ?? 'var(--text-secondary)' }}>{e.method}</span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span className="mono" style={{ fontSize: 11.5, textDecoration: e.deprecated ? 'line-through' : undefined }}>{e.path}</span>
                  {e.summary && <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}> · {e.summary}</span>}
                </span>
                <span style={{ fontSize: 10.5, flexShrink: 0, color: e.tried ? 'var(--accent)' : 'var(--text-quiet)' }}>
                  {e.tried ? `tried ${e.tried}×${e.last_status ? ` · ${e.last_status}` : ''}` : 'not tried'}
                </span>
              </button>
            ))}
          </div>
        ))}
        {shown.length === 0 && <div style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: 8 }}>No endpoint matches "{query}".</div>}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <button type="button" onClick={() => setImporting(true)} style={small}>Import another</button>
        {specs.find((s) => s.id === specId)?.source && (
          <button type="button" style={small} onClick={async () => {
            try { await api.refreshOpenApi(project.id, specId); setOps(await api.openApiEndpoints(project.id, specId)) } catch (e) { setError(errorText(e)) }
          }}>Refresh from its address</button>
        )}
        <ConfirmDelete label="Remove spec" question="Remove this spec from the project?" onConfirm={async () => {
          await api.deleteOpenApi(project.id, specId)
          const rest = specs.filter((s) => s.id !== specId)
          setSpecs(rest); setSpecId(rest[0]?.id ?? ''); setOps(null)
        }} />
      </div>
      <ErrorLine message={error} />
    </div>
  )
}
