import { useEffect, useState } from 'react'
import Sidebar from '../components/Sidebar'
import { api, BugListItem } from '../api/client'

const SEVERITY_COLOR: Record<string, string> = { high: 'var(--danger)', medium: 'var(--warning)', low: 'var(--text-tertiary)' }

export default function Bugs() {
  const [bugs, setBugs] = useState<BugListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    api.listProducts().then((ps) => {
      const p = ps[0]
      if (!p) return setLoading(false)
      api.listAllBugs(p.id).then((b) => { setBugs(b); setLoading(false) }).catch(console.error)
    }).catch(console.error)
  }, [])

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <div style={{ flex: 1, padding: '32px 40px', maxWidth: 800 }}>
        <div style={{ fontSize: 20, fontWeight: 600, marginBottom: 20 }}>Bugs</div>

        {loading && <div style={{ color: 'var(--text-secondary)' }}>Loading…</div>}
        {!loading && bugs.length === 0 && (
          <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>No bugs logged yet.</div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {bugs.map((b) => {
            const isOpen = expanded === b.id
            return (
              <div key={b.id} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '13px 16px' }}>
                <div
                  onClick={() => setExpanded(isOpen ? null : b.id)}
                  style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', cursor: 'pointer' }}
                >
                  <div>
                    <div style={{ fontSize: 13.5, fontWeight: 500 }}>{b.title}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--text-secondary)', marginTop: 4 }}>{b.charter_mission}</div>
                  </div>
                  <span className="mono" style={{ fontSize: 9.5, color: SEVERITY_COLOR[b.severity], flexShrink: 0, marginLeft: 12 }}>
                    {b.severity.toUpperCase()}
                  </span>
                </div>
                {isOpen && (
                  <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-soft)' }}>
                    <div className="mono" style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 8 }}>
                      Reproduction trail
                    </div>
                    {b.reproduction_trail.length === 0 && (
                      <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>No preceding notes captured.</div>
                    )}
                    {b.reproduction_trail.map((n, i) => (
                      <div key={i} style={{ display: 'flex', gap: 10, fontSize: 12, marginBottom: 4 }}>
                        <span className="mono" style={{ color: 'var(--text-quiet)', width: 70, flexShrink: 0 }}>
                          {new Date(n.timestamp).toLocaleTimeString()}
                        </span>
                        <span style={{ color: 'var(--text-secondary)' }}>[{n.tag}] {n.content}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}