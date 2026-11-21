import { useEffect, useState } from 'react'
import { api, type BugMagnetList } from '../api/client'

/** Bug Magnet's lists in the session: copy a tricky value into the app under test, then log the try.
 *  "Use in a note" starts a test note naming the value, for the tester to finish with what happened. */
export default function BugMagnetPanel({ onUse }: { onUse: (text: string) => void }) {
  const [lists, setLists] = useState<BugMagnetList[] | null>(null)
  const [open, setOpen] = useState('names')
  const [copied, setCopied] = useState<string | null>(null)
  useEffect(() => { api.bugMagnet().then(setLists).catch(() => setLists([])) }, [])
  if (!lists) return <div style={{ width: 360, fontSize: 12.5, color: 'var(--text-tertiary)' }}>Loading…</div>
  const shown = (v: string) => (v.length > 48 ? `${v.slice(0, 44)}… (${v.length} characters)` : v)
    .replace(/​/g, '⟨zero-width space⟩').replace(/‮/g, '⟨right-to-left override⟩')
    .replace(/\n/g, '⏎').replace(/\t/g, '⇥')
  return (
    <section aria-label="Bug Magnet" style={{ width: 360, border: '1px solid var(--border)', borderRadius: 10, background: 'var(--panel)', padding: '10px 12px' }}>
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 8 }}>
        Values that tend to break forms. Copy one into a field, see what the app does, then log it.
      </div>
      {lists.map((l) => (
        <div key={l.key} style={{ borderTop: '1px solid var(--border)' }}>
          <button aria-expanded={open === l.key} onClick={() => setOpen(open === l.key ? '' : l.key)} style={{
            width: '100%', textAlign: 'left', border: 'none', background: 'transparent', padding: '7px 0', fontSize: 13,
            fontWeight: 500, color: 'var(--text-primary)', cursor: 'pointer', fontFamily: 'var(--font-ui)',
          }}>{open === l.key ? '▾' : '▸'} {l.title} <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>({l.values.length})</span></button>
          {open === l.key && (
            <ul aria-label={l.title} style={{ listStyle: 'none', margin: 0, padding: '0 0 8px' }}>
              {l.values.map((v) => (
                <li key={v.what} aria-label={v.what} style={{ padding: '5px 0', fontSize: 12.5 }}>
                  <div style={{ color: 'var(--text-secondary)' }}>{v.what}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
                    <code className="mono" style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere', fontSize: 11.5 }}>{shown(v.value)}</code>
                    <button onClick={() => { navigator.clipboard?.writeText(v.value).catch(() => {}); setCopied(v.what) }}
                      style={smallButton}>{copied === v.what ? 'Copied' : 'Copy'}</button>
                    <button onClick={() => onUse(`Tried ${v.what} (Bug Magnet): `)}
                      title="Start a test note naming this value" style={smallButton}>Note</button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </section>
  )
}

const smallButton = {
  fontSize: 11, padding: '2px 8px', borderRadius: 5, border: '1px solid var(--border)',
  color: 'var(--text-secondary)', background: 'var(--bg)', cursor: 'pointer', flexShrink: 0,
} as const
