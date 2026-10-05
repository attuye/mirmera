/** The Mirmera app for Jira, for one workspace (admins only): which Jira sites are linked,
 *  and a one-time code to link another. The code is shown once and works for 30 minutes. */
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Copy, Link2 } from 'lucide-react'
import Sidebar from '../components/Sidebar'
import { utcDate } from '../components/Evidence'
import { ConfirmDelete, ErrorLine, errorText, primaryButton, quietButton } from '../components/ui'
import { api, JiraAppOverview, JiraLinkCode } from '../api/client'
import { useAuth } from '../auth'

const small: React.CSSProperties = { ...quietButton, fontSize: 12, padding: '5px 10px' }
const step: React.CSSProperties = { fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }

export default function JiraAppPage() {
  const { workspaceId = '' } = useParams()
  const { me } = useAuth()
  const [data, setData] = useState<JiraAppOverview | null>(null)
  const [code, setCode] = useState<JiraLinkCode | null>(null)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const workspace = me?.workspaces.find((w) => w.id === workspaceId)

  const load = () => api.jiraApp(workspaceId).then(setData).catch((e) => setError(errorText(e)))
  useEffect(() => { load() }, [workspaceId])

  async function makeCode() {
    setBusy(true); setError(null); setCopied(false)
    try { setCode(await api.jiraLinkCode(workspaceId)) } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '28px 40px 60px', maxWidth: 900 }}>
        <Link to="/account" style={{ fontSize: 12.5, color: 'var(--text-tertiary)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <ArrowLeft size={13} aria-hidden /> Account
        </Link>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: '10px 0 0', display: 'flex', gap: 10, alignItems: 'center' }}>
          <Link2 size={20} aria-hidden /> {workspace?.name ?? 'Workspace'}: Jira
        </h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '6px 0 0', lineHeight: 1.55, maxWidth: 720 }}>
          With the Mirmera app installed on a Jira site and linked to this workspace, every issue shows a Mirmera panel:
          the risks, charters and sessions behind a story, what hasn't been looked at yet, and what happened to the bugs
          Mirmera filed. Everyone who can open the issue in Jira sees the panel, so it shows counts, titles and what's
          left, never evidence, notes or keys.
        </p>
        <ErrorLine message={error} />

        {data && !data.configured && (
          <div role="note" style={{ marginTop: 16, padding: '10px 14px', borderRadius: 8, background: 'var(--warning-bg)', border: '1px solid var(--border-soft)', fontSize: 13, lineHeight: 1.55 }}>
            The Jira app isn't set up on this Mirmera server yet. Whoever runs it registers the app with Atlassian
            (<span className="mono">jira-app/README.md</span>) and puts its ID in <span className="mono">backend/.env</span> as{' '}
            <span className="mono">JIRA_APP_ID</span>. You can still make a code now.
          </div>
        )}

        {data && (
          <section aria-label="Linked Jira sites" style={{ borderTop: '1px solid var(--border-soft)', marginTop: 20, paddingTop: 14 }}>
            <h2 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 10px' }}>Linked Jira sites</h2>
            {data.installations.length === 0 && <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>None yet.</div>}
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {data.installations.map((s) => (
                <li key={s.id} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '8px 0', borderBottom: '1px solid var(--border-soft)', flexWrap: 'wrap' }}>
                  <span style={{ flex: 1, minWidth: 240 }}>
                    <span style={{ fontSize: 13.5 }}>{s.site_url ?? 'A Jira site'}</span>
                    {s.environment && s.environment !== 'PRODUCTION' && (
                      <span className="mono" style={{ fontSize: 10, color: 'var(--text-tertiary)', marginLeft: 8, textTransform: 'uppercase' }}>{s.environment.toLowerCase()}</span>
                    )}
                    <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>
                      Linked {utcDate(s.linked_at).toLocaleDateString()}{s.linked_by ? ` with ${s.linked_by}'s code` : ''}
                      {s.last_seen_at ? ` · last used ${utcDate(s.last_seen_at).toLocaleString()}` : ''}
                    </div>
                  </span>
                  <ConfirmDelete label="Unlink" busyLabel="Unlinking…" question="Unlink this Jira site? Its issues stop showing Mirmera."
                    onConfirm={async () => { await api.unlinkJiraSite(workspaceId, s.id); await load() }} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {data && (
          <section aria-label="Link a Jira site" style={{ borderTop: '1px solid var(--border-soft)', marginTop: 20, paddingTop: 14 }}>
            <h2 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 8px' }}>Link a Jira site</h2>
            <ol style={{ ...step, margin: 0, paddingLeft: 20 }}>
              <li>A Jira admin installs the Mirmera app on the site (the install link comes from whoever runs this Mirmera).</li>
              <li>Make a link code here. It is shown once and works for 30 minutes.</li>
              <li>In Jira: Settings → Apps → Mirmera, paste the code and choose Link to Mirmera.</li>
            </ol>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
              <button onClick={makeCode} disabled={busy} style={primaryButton(busy)}>{busy ? 'Making…' : code ? 'Make another code' : 'Make a link code'}</button>
              {code && (
                <>
                  <code aria-label="Link code" className="mono" style={{ fontSize: 13, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--panel-alt)', userSelect: 'all' }}>{code.code}</code>
                  <button style={{ ...small, display: 'inline-flex', gap: 5, alignItems: 'center' }} onClick={async () => {
                    try { await navigator.clipboard.writeText(code.code); setCopied(true) } catch { setCopied(false) }
                  }}><Copy size={13} aria-hidden />{copied ? 'Copied' : 'Copy'}</button>
                  <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Works once, until {utcDate(code.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}.</span>
                </>
              )}
            </div>
          </section>
        )}
      </main>
    </div>
  )
}
