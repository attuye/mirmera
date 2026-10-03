/** Shared form controls and error display, matching the app's design tokens. */
import { useState } from 'react'
import { API_URL } from '../api/client'

export const primaryButton = (disabled = false): React.CSSProperties => ({
  background: disabled ? 'var(--border)' : 'var(--accent)', color: disabled ? 'var(--text-tertiary)' : '#0C0D0F',
  fontSize: 12.5, fontWeight: 600, padding: '9px 16px', borderRadius: 8, cursor: disabled ? 'default' : 'pointer',
})
export const quietButton: React.CSSProperties = {
  background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border)',
  fontSize: 12, fontWeight: 500, padding: '6px 11px', borderRadius: 7,
}
export const input: React.CSSProperties = {
  width: '100%', background: 'var(--panel-alt)', border: '1px solid var(--border)', borderRadius: 8,
  color: 'var(--text)', padding: '8px 11px', fontSize: 13, fontFamily: 'var(--font-ui)',
}
export const fieldLabel: React.CSSProperties = { fontSize: 11.5, color: 'var(--text-tertiary)', marginBottom: 5, display: 'block' }

export function errorText(e: unknown): string {
  const message = e instanceof Error ? e.message : String(e)
  return message === 'Failed to fetch' ? `Could not reach the backend. Is it running on ${API_URL}?` : message.endsWith('.') ? message : `${message}.`
}

export function ErrorLine({ message }: { message: string | null }) {
  if (!message) return null
  return <div role="alert" style={{ color: 'var(--danger)', fontSize: 12.5, marginTop: 10 }}>{message}</div>
}


/** A delete button that asks once, inline: "Delete for good? Yes, delete / Cancel". */
export function ConfirmDelete({ label, question, onConfirm, busyLabel = 'Deleting…' }: {
  label: string; question: string; onConfirm: () => Promise<void>; busyLabel?: string
}) {
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const danger: React.CSSProperties = { ...quietButton, color: 'var(--danger)', borderColor: 'var(--danger-bg)' }
  if (!asking) return <button type="button" onClick={() => { setAsking(true); setError(null) }} style={danger}>{label}</button>
  return (
    <span role="group" aria-label={question} style={{ display: 'inline-flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <span style={{ fontSize: 12.5, color: 'var(--danger)' }}>{question}</span>
      <button type="button" disabled={busy} onClick={async () => {
        setBusy(true); setError(null)
        try { await onConfirm() } catch (e) { setError(errorText(e)); setBusy(false) }
      }} style={{ ...primaryButton(busy), background: busy ? 'var(--border)' : 'var(--danger)', color: '#fff', padding: '6px 12px' }}>
        {busy ? busyLabel : 'Yes, delete'}
      </button>
      <button type="button" disabled={busy} onClick={() => setAsking(false)} style={quietButton}>Cancel</button>
      {error && <span role="alert" style={{ color: 'var(--danger)', fontSize: 12.5, flexBasis: '100%' }}>{error}</span>}
    </span>
  )
}
