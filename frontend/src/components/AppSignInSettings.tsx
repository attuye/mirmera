/** Signing in to the app under test: set once, tried in a headless browser, used by every
 *  exploration run, its replays and the app survey. The password is never shown again. */
import { useEffect, useState } from 'react'
import { api, AppSignIn, AppSignInChanges, AppSignInTry } from '../api/client'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

const EMPTY: AppSignInChanges = { url: '', username_field: 'Email', username: '', password_field: 'Password', submit: '', signed_in_text: '', second_username: '' }
const FIELDS: { key: keyof AppSignInChanges; label: string; hint?: string; placeholder?: string }[] = [
  { key: 'url', label: 'Sign-in page', placeholder: '/login', hint: 'A path on the app URL, or a full address.' },
  { key: 'submit', label: 'Button', placeholder: 'Sign in', hint: "Its text. Empty: the form's own button." },
  { key: 'username_field', label: 'Username field', placeholder: 'Email', hint: "The field's label, id, name or placeholder." },
  { key: 'password_field', label: 'Password field', placeholder: 'Password' },
  { key: 'username', label: 'Username', placeholder: 'tester@example.com', hint: 'Use a test account, never a real customer.' },
]
const AFTER = { key: 'signed_in_text' as const, label: 'Words that show it worked', placeholder: 'Sign out',
  hint: 'Shown only when signed in, e.g. "Sign out" or "Welcome".' }

export default function AppSignInSettings({ projectId, hasAppUrl }: { projectId: string; hasAppUrl: boolean }) {
  const [saved, setSaved] = useState<AppSignIn | null | undefined>(undefined)
  const [form, setForm] = useState<AppSignInChanges>(EMPTY)
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [busy, setBusy] = useState<'save' | 'try' | 'remove' | null>(null)
  const [tried, setTried] = useState<AppSignInTry | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  const load = (s: AppSignIn | null) => {
    setSaved(s)
    setForm(s ? { url: s.url, username_field: s.username_field, username: s.username, password_field: s.password_field, submit: s.submit, signed_in_text: s.signed_in_text, second_username: s.second_username } : EMPTY)
    setPassword('')
    setPassword2('')
  }
  useEffect(() => { api.getAppSignIn(projectId).then(load).catch((e) => setError(errorText(e))) }, [projectId])
  if (saved === undefined) return <ErrorLine message={error} />

  const dirty = !!password || !!password2 || JSON.stringify(form) !== JSON.stringify(saved ? {
    url: saved.url, username_field: saved.username_field, username: saved.username, password_field: saved.password_field,
    submit: saved.submit, signed_in_text: saved.signed_in_text, second_username: saved.second_username } : EMPTY)

  async function act(what: 'save' | 'try' | 'remove') {
    setBusy(what); setError(null); setStatus(null)
    try {
      if (what === 'save') {
        if (!form.url.trim()) throw new Error('Give the sign-in page, e.g. /login.')
        load(await api.saveAppSignIn(projectId, { ...form, ...(password ? { password } : {}), ...(password2 ? { second_password: password2 } : {}) }))
        setStatus('Saved. Try it to be sure.')
        setTried(null)
      } else if (what === 'try') {
        setTried(await api.tryAppSignIn(projectId))
      } else {
        await api.deleteAppSignIn(projectId)
        load(null)
        setTried(null)
        setStatus('Removed: runs start signed out.')
      }
    } catch (e) { setError(errorText(e)) } finally { setBusy(null) }
  }

  return (
    <section aria-label="Signing in to the app" style={{ borderTop: '1px solid var(--border-soft)', padding: '22px 0', display: 'grid', gridTemplateColumns: '220px 1fr', gap: 24 }}>
      <div>
        <h2 style={{ fontSize: 14.5, fontWeight: 600, margin: 0 }}>Signing in to the app</h2>
        <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '5px 0 0', lineHeight: 1.5 }}>
          For an app behind a sign-in. Mirmera's runs, their replays and the app survey sign in first, then test as this user.
          The password is stored encrypted, never shown again, and never given to the AI.
        </p>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 560 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          {FIELDS.map((f) => (
            <div key={f.key}>
              <label htmlFor={`si-${f.key}`} style={fieldLabel}>{f.label}</label>
              <input id={`si-${f.key}`} value={(form[f.key] as string) ?? ''} placeholder={f.placeholder} style={input}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} autoComplete="off" />
              {f.hint && <div style={{ fontSize: 11.5, color: 'var(--text-quiet)', marginTop: 4 }}>{f.hint}</div>}
            </div>
          ))}
          <div>
            <label htmlFor="si-password" style={fieldLabel}>Password</label>
            <input id="si-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} style={input}
              placeholder={saved?.password_set ? 'Saved: type to replace' : ''} autoComplete="new-password" />
          </div>
          <div style={{ gridColumn: '1 / -1', fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>
            Second user (optional): after a run, Mirmera opens the first user's own pages (an order, a document) as this
            user and signed out. Seeing them there is a bug.
          </div>
          <div>
            <label htmlFor="si-second_username" style={fieldLabel}>Second username</label>
            <input id="si-second_username" value={form.second_username} onChange={(e) => setForm({ ...form, second_username: e.target.value })}
              style={input} placeholder="another-tester@example.com" autoComplete="off" />
          </div>
          <div>
            <label htmlFor="si-second_password" style={fieldLabel}>Second password</label>
            <input id="si-second_password" type="password" value={password2} onChange={(e) => setPassword2(e.target.value)} style={input}
              placeholder={saved?.second_password_set ? 'Saved: type to replace' : ''} autoComplete="new-password" />
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label htmlFor={`si-${AFTER.key}`} style={fieldLabel}>{AFTER.label}</label>
            <input id={`si-${AFTER.key}`} value={form[AFTER.key] ?? ''} placeholder={AFTER.placeholder} style={input}
              onChange={(e) => setForm({ ...form, [AFTER.key]: e.target.value })} autoComplete="off" />
            <div style={{ fontSize: 11.5, color: 'var(--text-quiet)', marginTop: 4 }}>{AFTER.hint}</div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button onClick={() => act('save')} disabled={!dirty || !!busy} style={primaryButton(!dirty || !!busy)}>
            {busy === 'save' ? 'Saving…' : 'Save sign-in'}
          </button>
          <button onClick={() => act('try')} disabled={!saved || dirty || !hasAppUrl || !!busy}
            title={!saved ? 'Save it first' : dirty ? 'Save your changes first' : !hasAppUrl ? 'Set the app URL first' : undefined} style={quietButton}>
            {busy === 'try' ? 'Signing in…' : 'Try sign-in'}
          </button>
          {saved && <button onClick={() => act('remove')} disabled={!!busy} style={quietButton}>Remove</button>}
          {status && <span role="status" style={{ fontSize: 12.5, color: 'var(--accent)' }}>{status}</span>}
        </div>
        <ErrorLine message={error} />
        {tried && (
          <div role="region" aria-label="Sign-in result" style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: tried.ok ? 'var(--accent)' : 'var(--danger)' }}>
              {tried.ok ? 'Signed in.' : "Couldn't sign in."}
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 3 }}>{tried.detail}</div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)', marginTop: 3 }}>{tried.url}</div>
            {tried.screenshot && <img src={tried.screenshot} alt="The app after signing in" style={{ width: '100%', marginTop: 8, borderRadius: 6, border: '1px solid var(--border-soft)' }} />}
          </div>
        )}
      </div>
    </section>
  )
}
