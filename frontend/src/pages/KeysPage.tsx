/** A workspace's keys for GitHub, Jira, AI providers, Telegram and Slack (admins only).
 *  Secrets are typed in and never shown again: only whether each is set and how it ends. */
import { Fragment, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, KeyRound } from 'lucide-react'
import Sidebar from '../components/Sidebar'
import { utcDate } from '../components/Evidence'
import { ErrorLine, errorText, input, primaryButton, quietButton } from '../components/ui'
import { api, WorkspaceKey, WorkspaceKeys } from '../api/client'
import { useAuth } from '../auth'

function State({ k }: { k: WorkspaceKey }) {
  if (k.unreadable) return <span style={{ color: 'var(--danger)' }}>Saved, but can't be read (the server key changed): enter it again</span>
  if (k.set) return <span><span style={{ color: 'var(--accent)' }}>Set</span> <span className="mono">{k.shown}</span>
    {k.updated_by && <span style={{ color: 'var(--text-tertiary)' }}> · {k.updated_by}, {k.updated_at && utcDate(k.updated_at).toLocaleDateString()}</span>}</span>
  return <span style={{ color: 'var(--text-tertiary)' }}>{k.server_fallback ? "Not set: the server's key from backend/.env is used" : 'Not set'}</span>
}

export default function KeysPage() {
  const { workspaceId = '' } = useParams()
  const { me } = useAuth()
  const [data, setData] = useState<WorkspaceKeys | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const workspace = me?.workspaces.find((w) => w.id === workspaceId)

  useEffect(() => { api.workspaceKeys(workspaceId).then(setData).catch((e) => setError(errorText(e))) }, [workspaceId])

  async function save(values: Record<string, string>, message: string) {
    setBusy(true); setError(null); setStatus(null)
    try {
      setData(await api.saveWorkspaceKeys(workspaceId, values))
      setDraft((d) => Object.fromEntries(Object.entries(d).filter(([n]) => !(n in values))))
      setStatus(message)
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const changes = Object.fromEntries(Object.entries(draft).filter(([, v]) => v.trim()))
  const groups = data ? [...new Set(data.keys.map((k) => k.group))] : []

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '28px 40px 60px', maxWidth: 960 }}>
        <Link to="/account" style={{ fontSize: 12.5, color: 'var(--text-tertiary)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <ArrowLeft size={13} aria-hidden /> Account
        </Link>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: '10px 0 0', display: 'flex', gap: 10, alignItems: 'center' }}>
          <KeyRound size={20} aria-hidden /> {workspace?.name ?? 'Workspace'}: keys
        </h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '6px 0 0', lineHeight: 1.55, maxWidth: 720 }}>
          Keys this workspace's projects use to file bugs, import, run AI tasks and send notifications. They're encrypted
          on the server and never shown again after you save them, only how they end. Only admins see this page.
          {data && (data.server_keys_for_all
            ? " Until a key is set here, the server's own from backend/.env is used."
            : ' This server shares no keys: each workspace brings its own.')}
        </p>
        <ErrorLine message={error} />

        {data && (
          <form onSubmit={(e) => { e.preventDefault(); save(changes, 'Keys saved.') }} aria-label="Workspace keys">
            {groups.map((g) => (
              <section key={g} aria-label={g} style={{ borderTop: '1px solid var(--border-soft)', marginTop: 20, paddingTop: 14 }}>
                <h2 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 10px' }}>{g}</h2>
                <div style={{ display: 'grid', gridTemplateColumns: '210px 1fr 84px', gap: '12px 16px', alignItems: 'start' }}>
                  {data.keys.filter((k) => k.group === g).map((k) => (
                    <Fragment key={k.name}>
                      <label htmlFor={`key-${k.name}`} style={{ fontSize: 13, paddingTop: 7 }}>{k.label}</label>
                      <div>
                        <input id={`key-${k.name}`} type={k.secret ? 'password' : 'text'} autoComplete="off" spellCheck={false}
                          value={draft[k.name] ?? ''} onChange={(e) => setDraft({ ...draft, [k.name]: e.target.value })}
                          placeholder={k.set ? (k.secret ? 'Paste a new value to replace it' : (k.shown ?? '')) : k.hint}
                          style={{ ...input, fontFamily: k.secret ? 'var(--font-mono)' : undefined }} />
                        <div style={{ fontSize: 11.5, marginTop: 4 }}><State k={k} /></div>
                      </div>
                      <div style={{ paddingTop: 2 }}>
                        {k.set || k.unreadable
                          ? <button type="button" disabled={busy} onClick={() => save({ [k.name]: '' }, `${k.label} removed.`)}
                              aria-label={`Remove ${k.label}`} style={{ ...quietButton, fontSize: 12, padding: '5px 10px' }}>Remove</button>
                          : <span />}
                      </div>
                    </Fragment>
                  ))}
                </div>
              </section>
            ))}
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 22 }}>
              <button type="submit" disabled={busy || !Object.keys(changes).length} style={primaryButton(busy || !Object.keys(changes).length)}>
                {busy ? 'Saving…' : 'Save keys'}
              </button>
              {status && <span role="status" style={{ fontSize: 12.5, color: 'var(--accent)' }}>{status}</span>}
            </div>
          </form>
        )}
      </main>
    </div>
  )
}
