/** Forgot password (asks for a reset link) and reset (chooses a new password from the link). */
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import AuthFrame from '../components/AuthFrame'
import { ErrorLine, errorText, fieldLabel, input, primaryButton } from '../components/ui'
import { api } from '../api/client'
import { useAuth } from '../auth'

export function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (done) return (
    <AuthFrame title="Check your email">
      <p role="status" style={{ fontSize: 13, color: 'var(--text-secondary)', margin: 0, lineHeight: 1.5 }}>
        If <b>{email}</b> has an account, a link to choose a new password is on its way (it works for an hour). Nothing
        arriving? The person running this Mirmera can find the link in the server log when email isn't set up.
      </p>
      <Link to="/signin" style={{ fontSize: 13 }}>Back to sign in</Link>
    </AuthFrame>
  )
  return (
    <AuthFrame title="Forgot your password?">
      <form onSubmit={async (e) => {
        e.preventDefault(); setBusy(true); setError(null)
        try { await api.forgotPassword(email.trim()); setDone(true) } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
      }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div><label htmlFor="forgot-email" style={fieldLabel}>Email</label>
          <input id="forgot-email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} style={input} /></div>
        <ErrorLine message={error} />
        <button type="submit" disabled={busy || !email.includes('@')} style={primaryButton(busy || !email.includes('@'))}>{busy ? 'Sending…' : 'Send a reset link'}</button>
      </form>
      <Link to="/signin" style={{ fontSize: 12.5 }}>Back to sign in</Link>
    </AuthFrame>
  )
}

export function ResetPassword() {
  const { token = '' } = useParams()
  const { setMe } = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <AuthFrame title="Choose a new password">
      <p style={{ fontSize: 12.5, color: 'var(--text-tertiary)', margin: 0 }}>Every browser signed in to your account will be signed out.</p>
      <form onSubmit={async (e) => {
        e.preventDefault(); setBusy(true); setError(null)
        try { setMe(await api.resetPassword(token, password)); navigate('/projects') } catch (err) { setError(errorText(err)); setBusy(false) }
      }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div><label htmlFor="reset-password" style={fieldLabel}>New password (at least 10 characters)</label>
          <input id="reset-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} style={input} /></div>
        <ErrorLine message={error} />
        <button type="submit" disabled={busy || password.length < 10} style={primaryButton(busy || password.length < 10)}>{busy ? 'Saving…' : 'Save and sign in'}</button>
      </form>
      {error && <Link to="/forgot" style={{ fontSize: 12.5 }}>Ask for a new link</Link>}
    </AuthFrame>
  )
}
