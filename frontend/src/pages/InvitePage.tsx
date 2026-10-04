/** An invitation link: join the workspace, signing in or creating an account for the invited email. */
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import AuthFrame from '../components/AuthFrame'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from '../components/ui'
import { api, InvitationView } from '../api/client'
import { useAuth } from '../auth'

export default function InvitePage() {
  const { token = '' } = useParams()
  const { me, loading, setMe, signOut, setWorkspace } = useAuth()
  const navigate = useNavigate()
  const [invite, setInvite] = useState<InvitationView | null>(null)
  const [form, setForm] = useState({ name: '', password: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dead, setDead] = useState<string | null>(null)

  useEffect(() => { api.viewInvitation(token).then(setInvite).catch((e) => setDead(errorText(e))) }, [token])

  async function joined(fn: () => Promise<import('../api/client').Me>) {
    setBusy(true); setError(null)
    try {
      const m = await fn()
      setMe(m)
      const ws = m.workspaces.find((w) => w.name === invite?.workspace_name)
      if (ws) setWorkspace(ws.id)
      navigate('/projects')
    } catch (e) { setError(errorText(e)); setBusy(false) }
  }

  if (dead) return <AuthFrame title="Invitation"><p role="alert" style={{ fontSize: 13, color: 'var(--danger)', margin: 0 }}>{dead}</p><Link to="/signin" style={{ fontSize: 13 }}>Go to sign in</Link></AuthFrame>
  if (!invite || loading) return <AuthFrame title="Invitation"><p style={{ fontSize: 13, margin: 0 }}>Loading…</p></AuthFrame>
  const title = `Join ${invite.workspace_name}`
  const about = <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: 0, lineHeight: 1.5 }}>
    {invite.invited_by ?? 'Someone'} invited <b>{invite.email}</b> to the workspace <b>{invite.workspace_name}</b> as a <b>{invite.role}</b>.
  </p>

  if (me && me.user.email === invite.email) return (
    <AuthFrame title={title}>{about}<ErrorLine message={error} />
      <button onClick={() => joined(() => api.acceptInvitation(token))} disabled={busy} style={primaryButton(busy)}>{busy ? 'Joining…' : `Join ${invite.workspace_name}`}</button>
    </AuthFrame>
  )
  if (me) return (
    <AuthFrame title={title}>{about}
      <p style={{ fontSize: 13, margin: 0 }}>You're signed in as <b>{me.user.email}</b>. Sign out, then open this link again to accept it as {invite.email}.</p>
      <button onClick={() => signOut()} style={quietButton}>Sign out</button>
    </AuthFrame>
  )
  if (invite.has_account) return (
    <AuthFrame title={title}>{about}
      <form onSubmit={(e) => { e.preventDefault(); joined(async () => { await api.signIn(invite.email, form.password); return api.acceptInvitation(token) }) }}
        style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div><label htmlFor="inv-password" style={fieldLabel}>Your password for {invite.email}</label>
          <input id="inv-password" type="password" autoComplete="current-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} style={input} /></div>
        <ErrorLine message={error} />
        <button type="submit" disabled={busy || !form.password} style={primaryButton(busy || !form.password)}>{busy ? 'Joining…' : 'Sign in and join'}</button>
      </form>
      <Link to="/forgot" style={{ fontSize: 12.5 }}>Forgot your password?</Link>
    </AuthFrame>
  )
  return (
    <AuthFrame title={title}>{about}
      <form onSubmit={(e) => { e.preventDefault(); joined(() => api.signUpByInvitation(token, form.name, form.password)) }}
        style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div><label htmlFor="inv-name" style={fieldLabel}>Your name</label>
          <input id="inv-name" autoComplete="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={input} maxLength={100} /></div>
        <div><label htmlFor="inv-new-password" style={fieldLabel}>Choose a password (at least 10 characters)</label>
          <input id="inv-new-password" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} style={input} /></div>
        <ErrorLine message={error} />
        <button type="submit" disabled={busy || !form.name.trim() || form.password.length < 10} style={primaryButton(busy || !form.name.trim() || form.password.length < 10)}>
          {busy ? 'Joining…' : 'Create account and join'}
        </button>
      </form>
    </AuthFrame>
  )
}
