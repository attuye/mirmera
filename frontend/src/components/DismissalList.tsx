/** What Mirmera won't draft again in this project: the drafts the team dismissed, and why.
 *  Forgetting one lets Mirmera draft that problem again. */
import { useEffect, useState } from 'react'
import { api, DISMISS_REASONS, Dismissal } from '../api/client'
import { ErrorLine, errorText, quietButton } from './ui'

export default function DismissalList({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<Dismissal[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.dismissals(projectId).then(setRows).catch((e) => setError(errorText(e))) }, [projectId])
  if (!rows) return <ErrorLine message={error} />
  return (
    <section aria-label="Dismissed problems" style={{ marginTop: 28 }}>
      <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 4px' }}>What Mirmera won't draft again</h2>
      <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', margin: '0 0 10px', lineHeight: 1.5 }}>
        Drafts the team dismissed, and why. Mirmera skips these problems in this project, and its AI is told why.
      </p>
      {rows.length === 0 ? <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)' }}>Nothing dismissed yet.</div> : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {rows.map((d) => (
            <li key={d.id} aria-label={d.title} style={{ display: 'flex', gap: 10, alignItems: 'baseline', fontSize: 12.5,
              border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px' }}>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block' }}>{d.title}</span>
                <span style={{ color: 'var(--text-tertiary)', fontSize: 11.5 }}>
                  {DISMISS_REASONS[d.reason]}{d.by ? ` · ${d.by}` : ''} · {new Date(d.created_at).toLocaleDateString()}{d.note ? ` · "${d.note}"` : ''}
                </span>
              </span>
              <button style={{ ...quietButton, fontSize: 11.5, padding: '3px 9px' }} onClick={async () => {
                try { await api.forgetDismissal(d.id); setRows(rows.filter((x) => x.id !== d.id)) } catch (e) { setError(errorText(e)) }
              }}>Forget</button>
            </li>
          ))}
        </ul>
      )}
      <ErrorLine message={error} />
    </section>
  )
}
