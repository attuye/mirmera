/** A charter's mission, editable in place. While editing, a hint names the parts of Elisabeth
 *  Hendrickson's template the text is missing ("Explore <target> with <resources> to discover
 *  <information>"): the "with" and "to discover" parts are what steer a session, a person's or
 *  Mirmera's agent's. Saving is up to the caller (a suggestion keeps it until accepted; a saved
 *  charter is updated through the API, which refuses once a session has used it). */
import { useState } from 'react'
import { errorText, input, quietButton } from './ui'

const small: React.CSSProperties = { ...quietButton, fontSize: 12, padding: '4px 10px' }

/** The template's parts the mission doesn't have yet. */
export function missingParts(mission: string): string[] {
  const t = mission.toLowerCase()
  return [!/\bwith\b/.test(t) && 'with … (what you will use)', !/\bto discover\b/.test(t) && 'to discover … (the question it answers)']
    .filter(Boolean) as string[]
}

export default function MissionEditor({ value, onSave, canEdit = true, style, why }: {
  value: string
  onSave: (mission: string) => Promise<void> | void
  canEdit?: boolean
  style?: React.CSSProperties
  why?: string  // shown as the Edit button's tooltip when editing isn't possible
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const missing = missingParts(draft)

  async function save() {
    const text = draft.trim()
    if (text.length < 10) { setError('Write the mission in a sentence: at least 10 characters.'); return }
    setSaving(true); setError(null)
    try { await onSave(text); setEditing(false) } catch (e) { setError(errorText(e)) } finally { setSaving(false) }
  }

  if (!editing) {
    return (
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0, ...style }}>{value}</div>
        {canEdit
          ? <button onClick={(e) => { e.stopPropagation(); setDraft(value); setError(null); setEditing(true) }} style={small}
              aria-label={`Edit mission: ${value.slice(0, 60)}`}>Edit</button>
          : why ? <span title={why} style={{ fontSize: 11, color: 'var(--text-quiet)', whiteSpace: 'nowrap' }}>can't edit</span> : null}
      </div>
    )
  }
  return (
    <div onClick={(e) => e.stopPropagation()} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <textarea aria-label="Mission" value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus rows={3}
        onKeyDown={(e) => { if (e.key === 'Escape') setEditing(false); if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) save() }}
        placeholder="Explore <target> with <resources> to discover <information>"
        style={{ ...input, fontSize: 13, lineHeight: 1.45, resize: 'vertical' }} />
      {missing.length > 0 && (
        <div role="note" aria-label="Template hint" style={{ fontSize: 11.5, color: 'var(--warning)' }}>
          A charter reads "Explore … with … to discover …". Missing: {missing.join('; ')}.
        </div>
      )}
      {error && <div role="alert" style={{ fontSize: 12, color: 'var(--danger)' }}>{error}</div>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={save} disabled={saving} style={{ ...small, color: 'var(--accent)' }}>{saving ? 'Saving…' : 'Save'}</button>
        <button onClick={() => setEditing(false)} disabled={saving} style={small}>Cancel</button>
      </div>
    </div>
  )
}
