/** A workspace's members and invitations: invite by email (emailed, or a link to send), change
 *  roles, remove people, leave. Everyone in the workspace can see who's in; admins change it. */
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import Sidebar from '../components/Sidebar'
import { utcDate } from '../components/Evidence'
import { ConfirmDelete, ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from '../components/ui'
import { api, Members, Role } from '../api/client'
import { useAuth } from '../auth'

const ROLES: { key: Role; about: string }[] = [
  { key: 'viewer', about: 'looks at everything' },
  { key: 'tester', about: 'tests: sessions, bugs, risks, charters, stories' },
  { key: 'admin', about: 'also projects, settings and members' },
  { key: 'owner', about: 'the same as admin; a workspace needs one' },
]
const small: React.CSSProperties = { ...quietButton, fontSize: 12, padding: '4px 10px' }
const select: React.CSSProperties = { ...input, width: 'auto', fontSize: 12, padding: '3px 6px' }

export default function MembersPage() {
  const { workspaceId = '' } = useParams()
  const { me, setMe } = useAuth()
  const navigate = useNavigate()
  const [data, setData] = useState<Members | null>(null)
  const [form, setForm] = useState<{ email: string; role: Role }>({ email: '', role: 'tester' })
  const [sent, setSent] = useState<{ email: string; link: string; emailed: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.members(workspaceId).then(setData).catch((e) => setError(errorText(e))) }, [workspaceId])
  const mine = me?.workspaces.find((w) => w.id === workspaceId)?.role
  const isAdmin = mine === 'admin' || mine === 'owner'
  const roles = ROLES.filter((r) => r.key !== 'owner' || mine === 'owner')

  async function act(fn: () => Promise<Members | void>) {
    setError(null)
    try { const d = await fn(); if (d) setData(d) } catch (e) { setError(errorText(e)) }
  }
  async function invite(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError(null); setSent(null)
    try {
      const r = await api.invite(workspaceId, form.email.trim(), form.role)
      setSent({ email: r.email, link: r.link, emailed: r.emailed })
      setForm({ email: '', role: form.role })
      setData(await api.members(workspaceId))
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }

  if (!data) return <div style={{ display: 'flex', minHeight: '100vh' }}><Sidebar /><main style={{ padding: '32px 40px' }}>{error ? <ErrorLine message={error} /> : 'Loading…'}</main></div>
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '28px 40px', maxWidth: 900 }}>
        <Link to="/account" style={{ fontSize: 12.5, color: 'var(--text-tertiary)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <ArrowLeft size={13} aria-hidden /> Account
        </Link>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: '10px 0 0' }}>{data.workspace.name}: members</h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0', lineHeight: 1.5 }}>
          Everyone here sees every project in the workspace. Roles: {ROLES.map((r, i) => <span key={r.key}>{i > 0 && ' · '}<b>{r.key}</b> {r.about}</span>)}.
        </p>
        <ErrorLine message={error} />

        {isAdmin && (
          <section aria-label="Invite" style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 16, marginTop: 20 }}>
            <form onSubmit={invite} style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
              <div style={{ flex: 1 }}>
                <label htmlFor="invite-email" style={fieldLabel}>Invite by email</label>
                <input id="invite-email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} style={input} placeholder="sara@example.com" />
              </div>
              <div>
                <label htmlFor="invite-role" style={fieldLabel}>Role</label>
                <select id="invite-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })} style={{ ...input, width: 110 }}>
                  {roles.map((r) => <option key={r.key} value={r.key}>{r.key}</option>)}
                </select>
              </div>
              <button type="submit" disabled={busy || !form.email.includes('@')} style={primaryButton(busy || !form.email.includes('@'))}>{busy ? 'Inviting…' : 'Invite'}</button>
            </form>
            {!data.email_configured && !sent && (
              <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 6 }}>Email isn't set up on this server (SMTP_HOST), so you'll get a link to send yourself.</div>
            )}
            {sent && (
              <div role="status" style={{ marginTop: 12, fontSize: 12.5 }}>
                {sent.emailed ? <>Invitation emailed to <b>{sent.email}</b>. You can also send them the link:</> : <>Send this link to <b>{sent.email}</b> (it works for 7 days, only for that email):</>}
                <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
                  <input readOnly aria-label="Invitation link" value={sent.link} className="mono" style={{ ...input, fontSize: 11.5 }} onFocus={(e) => e.target.select()} />
                  <button onClick={() => navigator.clipboard?.writeText(sent.link)} style={quietButton}>Copy</button>
                </div>
              </div>
            )}
          </section>
        )}

        <section aria-label="Members" style={{ marginTop: 24 }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 8px' }}>Members <span style={{ fontWeight: 400, color: 'var(--text-tertiary)', fontSize: 13 }}>{data.members.length}</span></h2>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {data.members.map((m) => {
              const self = m.user_id === me?.user.id
              const canChange = isAdmin && !self && (m.role !== 'owner' || mine === 'owner')
              return (
                <li key={m.user_id} aria-label={m.name} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '9px 0', borderBottom: '1px solid var(--border-soft)', fontSize: 13 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div>{m.name}{self && <span style={{ color: 'var(--text-tertiary)' }}> (you)</span>}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{m.email} · joined {utcDate(m.joined_at).toLocaleDateString()}</div>
                  </div>
                  {canChange
                    ? <select aria-label={`Role of ${m.name}`} value={m.role} onChange={(e) => act(() => api.changeRole(workspaceId, m.user_id, e.target.value as Role))} style={select}>
                        {roles.map((r) => <option key={r.key} value={r.key}>{r.key}</option>)}
                      </select>
                    : <span className="mono" style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>{m.role}</span>}
                  {canChange && <ConfirmDelete label="Remove" question={`Remove ${m.name}? Their sessions and bugs stay.`}
                    onConfirm={() => act(() => api.removeMember(workspaceId, m.user_id))} />}
                  {self && <ConfirmDelete label="Leave" question="Leave this workspace?" onConfirm={async () => {
                    setMe(await api.leaveWorkspace(workspaceId)); navigate('/account')
                  }} />}
                </li>
              )
            })}
          </ul>
        </section>

        {data.invitations.length > 0 && (
          <section aria-label="Pending invitations" style={{ marginTop: 24 }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 8px' }}>Pending invitations</h2>
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {data.invitations.map((i) => (
                <li key={i.id} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '8px 0', borderBottom: '1px solid var(--border-soft)', fontSize: 13 }}>
                  <span style={{ flex: 1 }}>{i.email} <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>as {i.role}{i.invited_by ? `, by ${i.invited_by}` : ''}</span></span>
                  <span style={{ fontSize: 11.5, color: i.expired ? 'var(--danger)' : 'var(--text-tertiary)' }}>
                    {i.expired ? 'expired' : `until ${utcDate(i.expires_at).toLocaleDateString()}`}
                  </span>
                  {isAdmin && <button onClick={() => act(async () => { await api.revokeInvitation(workspaceId, i.id); return api.members(workspaceId) })} style={small}>Revoke</button>}
                </li>
              ))}
            </ul>
            {isAdmin && <p style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>To send a link again, invite the same email: the old link stops working.</p>}
          </section>
        )}
      </main>
    </div>
  )
}
