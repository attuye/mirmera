/** Sign in, or create an account with its own workspace. The very first account also takes
 *  over the projects made before accounts existed. */
import { useEffect, useState } from 'react'
import { Navigate, useSearchParams } from 'react-router-dom'
import { ErrorLine, errorText, fieldLabel, input, primaryButton } from '../components/ui'
import { api, AuthConfig } from '../api/client'
import { useAuth } from '../auth'

type Mode = 'signin' | 'signup'

export default function SignIn() {
  const { me, setMe } = useAuth()
  const [params] = useSearchParams()
  const next = params.get('next') || '/'
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [mode, setMode] = useState<Mode>('signin')
  const [form, setForm] = useState({ name: '', email: '', password: '', workspace_name: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.authConfig().then((c) => { setConfig(c); if (!c.has_users) setMode('signup') }).catch((e) => setError(errorText(e)))
  }, [])
  if (me) return <Navigate to={next.startsWith('/') ? next : '/'} replace />

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      setMe(mode === 'signin' ? await api.signIn(form.email, form.password)
        : await api.signUp({ ...form, workspace_name: form.workspace_name.trim() || undefined }))
    } catch (err) { setError(errorText(err)); setBusy(false) }
  }
  const field = (id: keyof typeof form, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <div>
      <label htmlFor={`auth-${id}`} style={fieldLabel}>{label}</label>
      <input id={`auth-${id}`} value={form[id]} onChange={(e) => setForm({ ...form, [id]: e.target.value })} style={input} {...props} />
    </div>
  )
  const first = config && !config.has_users

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <main style={{ width: '100%', maxWidth: 400 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 24 }}>
          <img src="/mirmera.svg" width={36} height={36} alt="" />
          <div>
            <div style={{ fontWeight: 600, fontSize: 17 }}>Mirmera</div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Context-driven exploratory testing</div>
          </div>
        </div>
        <form aria-label={mode === 'signin' ? 'Sign in' : 'Create account'} onSubmit={submit} style={{
          background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, padding: '22px 22px 18px',
          display: 'flex', flexDirection: 'column', gap: 12,
        }}>
          <h1 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>{mode === 'signin' ? 'Sign in' : first ? 'Create the first account' : 'Create an account'}</h1>
          {first && config.unowned_projects > 0 && (
            <p role="note" style={{ fontSize: 12.5, color: 'var(--text-secondary)', margin: 0, lineHeight: 1.5 }}>
              {config.unowned_projects} existing project{config.unowned_projects === 1 ? '' : 's'} will move into your new workspace.
            </p>
          )}
          {mode === 'signup' && field('name', 'Your name', { autoComplete: 'name', required: true, maxLength: 100 })}
          {field('email', 'Email', { type: 'email', autoComplete: 'email', required: true })}
          {field('password', 'Password', {
            type: 'password', required: true, autoComplete: mode === 'signin' ? 'current-password' : 'new-password',
            minLength: mode === 'signup' ? 10 : undefined,
          })}
          {mode === 'signup' && <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: -6 }}>At least 10 characters; a short phrase is easier to remember.</div>}
          {mode === 'signup' && field('workspace_name', 'Workspace (your team or company)', { placeholder: 'e.g. AddisVerify', maxLength: 100 })}
          <ErrorLine message={error} />
          <button type="submit" disabled={busy} style={{ ...primaryButton(busy), marginTop: 4 }}>
            {busy ? 'One moment…' : mode === 'signin' ? 'Sign in' : 'Create account'}
          </button>
        </form>
        {config && !first && (
          <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', textAlign: 'center', marginTop: 14 }}>
            {mode === 'signin'
              ? config.allow_signups
                ? <>New here? <button onClick={() => { setMode('signup'); setError(null) }} style={linkButton}>Create an account</button></>
                : 'New accounts are by invitation; ask a workspace admin.'
              : <>Have an account? <button onClick={() => { setMode('signin'); setError(null) }} style={linkButton}>Sign in</button></>}
          </p>
        )}
      </main>
    </div>
  )
}

const linkButton: React.CSSProperties = { background: 'none', border: 'none', padding: 0, color: 'var(--accent)', cursor: 'pointer', font: 'inherit' }
