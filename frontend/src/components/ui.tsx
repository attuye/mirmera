/** Shared form controls and error display, matching the app's design tokens. */
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
  return message === 'Failed to fetch' ? 'Could not reach the backend. Is it running on http://localhost:8000?' : message.endsWith('.') ? message : `${message}.`
}

export function ErrorLine({ message }: { message: string | null }) {
  if (!message) return null
  return <div role="alert" style={{ color: 'var(--danger)', fontSize: 12.5, marginTop: 10 }}>{message}</div>
}

