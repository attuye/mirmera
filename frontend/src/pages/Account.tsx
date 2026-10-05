/** Your account: name, password, workspaces, and API tokens for the Chrome extension. */
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import { utcDate } from '../components/Evidence'
import { ConfirmDelete, ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from '../components/ui'
import { api, ApiToken } from '../api/client'
import { useAuth } from '../auth'

function Section({ title, about, children }: { title: string; about?: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} style={{ borderTop: '1px solid var(--border-soft)', padding: '22px 0', display: 'grid', gridTemplateColumns: '220px 1fr', gap: 24 }}>
      <div>
        <h2 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>{title}</h2>
        {about && <p style={{ fontSize: 12.5, color: 'var(--text-tertiary)', margin: '4px 0 0', lineHeight: 1.5 }}>{about}</p>}
      </div>
      <div style={{ maxWidth: 520 }}>{children}</div>
    </section>
  )
}

function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  async function run(fn: () => Promise<unknown>, message: string) {
    setBusy(true); setError(null); setDone(null)
    try { await fn(); setDone(message) } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const status = <>{done && <span role="status" style={{ fontSize: 12.5, color: 'var(--accent)' }}>{done}</span>}<ErrorLine message={error} /></>
  return { busy, run, status }
}

function Profile() {
  const { me, setMe } = useAuth()
  const [name, setName] = useState(me!.user.name)
  const { busy, run, status } = useAction()
  return (
    <Section title="Profile">
      <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginBottom: 10 }}>Signed in as <b>{me!.user.email}</b></div>
      <label htmlFor="account-name" style={fieldLabel}>Name</label>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input id="account-name" value={name} onChange={(e) => setName(e.target.value)} style={input} maxLength={100} />
        <button disabled={busy || !name.trim() || name === me!.user.name} style={quietButton}
          onClick={() => run(async () => setMe(await api.updateMe(name)), 'Saved.')}>Save</button>
      </div>
      {status}
    </Section>
  )
}

function Password() {
  const [form, setForm] = useState({ current: '', next: '' })
  const { busy, run, status } = useAction()
  return (
    <Section title="Password" about="Changing it signs out your other browsers. Extension tokens keep working.">
      <form onSubmit={(e) => { e.preventDefault(); run(async () => { await api.changePassword(form.current, form.next); setForm({ current: '', next: '' }) }, 'Password changed.') }}
        style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div><label htmlFor="pw-current" style={fieldLabel}>Current password</label>
          <input id="pw-current" type="password" autoComplete="current-password" value={form.current} onChange={(e) => setForm({ ...form, current: e.target.value })} style={input} /></div>
        <div><label htmlFor="pw-new" style={fieldLabel}>New password (at least 10 characters)</label>
          <input id="pw-new" type="password" autoComplete="new-password" value={form.next} onChange={(e) => setForm({ ...form, next: e.target.value })} style={input} /></div>
        <div><button type="submit" disabled={busy || !form.current || form.next.length < 10} style={primaryButton(busy || !form.current || form.next.length < 10)}>Change password</button></div>
      </form>
      {status}
    </Section>
  )
}

function Workspaces() {
  const { me, setMe, workspace, setWorkspace } = useAuth()
  const [name, setName] = useState('')
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null)
  const { busy, run, status } = useAction()
  return (
    <Section title="Workspaces" about="A workspace is a team or company. Its projects are visible only to its members.">
      <ul aria-label="Your workspaces" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {me!.workspaces.map((w) => (
          <li key={w.id} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '8px 0', borderBottom: '1px solid var(--border-soft)', fontSize: 13 }}>
            {renaming?.id === w.id ? (
              <form style={{ display: 'flex', gap: 6, flex: 1 }} onSubmit={(e) => { e.preventDefault(); run(async () => { setMe(await api.renameWorkspace(w.id, renaming.name)); setRenaming(null) }, 'Renamed.') }}>
                <input aria-label="Workspace name" value={renaming.name} onChange={(e) => setRenaming({ ...renaming, name: e.target.value })} style={{ ...input, padding: '4px 8px' }} autoFocus />
                <button type="submit" disabled={busy || !renaming.name.trim()} style={quietButton}>Save</button>
                <button type="button" onClick={() => setRenaming(null)} style={quietButton}>Cancel</button>
              </form>
            ) : (
              <>
                <span style={{ flex: 1 }}>{w.name}{workspace?.id === w.id && <span style={{ fontSize: 11, color: 'var(--accent)' }}> · current</span>}</span>
                <span className="mono" style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>{w.role}</span>
                <Link to={`/workspaces/${w.id}/members`} style={{ ...quietButton, fontSize: 12, padding: '3px 9px' }}>Members</Link>
                {workspace?.id !== w.id && <button onClick={() => setWorkspace(w.id)} style={{ ...quietButton, fontSize: 12, padding: '3px 9px' }}>Switch</button>}
                {(w.role === 'owner' || w.role === 'admin') && <Link to={`/workspaces/${w.id}/keys`} style={{ ...quietButton, fontSize: 12, padding: '3px 9px' }}>Keys</Link>}
                {(w.role === 'owner' || w.role === 'admin') && <Link to={`/workspaces/${w.id}/jira`} style={{ ...quietButton, fontSize: 12, padding: '3px 9px' }}>Jira</Link>}
                {(w.role === 'owner' || w.role === 'admin') && <button onClick={() => setRenaming({ id: w.id, name: w.name })} style={{ ...quietButton, fontSize: 12, padding: '3px 9px' }}>Rename</button>}
              </>
            )}
          </li>
        ))}
      </ul>
      <form onSubmit={(e) => { e.preventDefault(); run(async () => { const m = await api.createWorkspace(name); setMe(m); setWorkspace(m.workspaces[m.workspaces.length - 1].id); setName('') }, 'Workspace created.') }}
        style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <input aria-label="New workspace name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Client B" style={input} maxLength={100} />
        <button type="submit" disabled={busy || !name.trim()} style={{ ...quietButton, whiteSpace: 'nowrap' }}>New workspace</button>
      </form>
      {status}
    </Section>
  )
}

function Tokens() {
  const [tokens, setTokens] = useState<ApiToken[]>([])
  const [label, setLabel] = useState('Chrome extension')
  const [fresh, setFresh] = useState<string | null>(null)
  const { busy, run, status } = useAction()
  useEffect(() => { api.listTokens().then(setTokens).catch(() => {}) }, [])
  return (
    <Section title="Chrome extension" about="The extension signs in with a token: create one here and paste it into the extension's settings. Delete it to sign the extension out.">
      {fresh && (
        <div role="note" aria-label="New token" style={{ background: 'var(--accent-bg)', borderRadius: 8, padding: '10px 12px', marginBottom: 12 }}>
          <div style={{ fontSize: 12.5, marginBottom: 6 }}>Copy this token now; it won't be shown again.</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input readOnly aria-label="Token" value={fresh} className="mono" style={{ ...input, fontSize: 11.5 }} onFocus={(e) => e.target.select()} />
            <button onClick={() => navigator.clipboard?.writeText(fresh)} style={quietButton}>Copy</button>
          </div>
        </div>
      )}
      <ul aria-label="Extension tokens" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {tokens.map((t) => (
          <li key={t.id} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '8px 0', borderBottom: '1px solid var(--border-soft)', fontSize: 13 }}>
            <span style={{ flex: 1 }}>{t.label}</span>
            <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{t.last_used_at ? `used ${utcDate(t.last_used_at).toLocaleDateString()}` : 'never used'}</span>
            <ConfirmDelete label="Delete" question="Delete this token?" onConfirm={async () => { await api.deleteToken(t.id); setTokens((p) => p.filter((x) => x.id !== t.id)) }} />
          </li>
        ))}
      </ul>
      <form onSubmit={(e) => { e.preventDefault(); run(async () => { const t = await api.createToken(label); setFresh(t.token); setTokens((p) => [t, ...p]) }, 'Token created.') }}
        style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <input aria-label="Token label" value={label} onChange={(e) => setLabel(e.target.value)} style={input} maxLength={80} />
        <button type="submit" disabled={busy || !label.trim()} style={{ ...quietButton, whiteSpace: 'nowrap' }}>Create token</button>
      </form>
      {status}
    </Section>
  )
}

export default function Account() {
  const { signOut } = useAuth()
  const navigate = useNavigate()
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1000 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Account</h1>
          <button onClick={async () => { await signOut(); navigate('/signin') }} style={quietButton}>Sign out</button>
        </div>
        <Profile />
        <Password />
        <Workspaces />
        <Tokens />
      </main>
    </div>
  )
}
