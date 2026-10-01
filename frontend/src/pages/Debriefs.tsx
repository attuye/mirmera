import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import { api, DebriefListItem } from '../api/client'

export default function DebriefsList() {
  const [debriefs, setDebriefs] = useState<DebriefListItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.listProducts().then((ps) => {
      const p = ps[0]
      if (!p) return setLoading(false)
      api.listDebriefs(p.id).then((d) => {
        setDebriefs(d)
        setLoading(false)
      }).catch(console.error)
    }).catch(console.error)
  }, [])

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <div style={{ flex: 1, padding: '32px 40px', maxWidth: 800 }}>
        <div style={{ fontSize: 20, fontWeight: 600, marginBottom: 20 }}>Debriefs</div>

        {loading && <div style={{ color: 'var(--text-secondary)' }}>Loading…</div>}
        {!loading && debriefs.length === 0 && (
          <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
            No debriefs yet — they show up here once you end a session.
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {debriefs.map((d) => (
            <Link key={d.id} to={`/sessions/${d.session_id}/debrief`} style={{
              display: 'block', border: '1px solid var(--border)', borderRadius: 10,
              padding: '14px 18px', color: 'var(--text)',
            }}>
              <div style={{ fontSize: 13.5, fontWeight: 500 }}>{d.charter_mission}</div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>
                {d.coverage_summary
                  ? d.coverage_summary.slice(0, 160) + (d.coverage_summary.length > 160 ? '…' : '')
                  : '(no summary yet)'}
              </div>
              <div className="mono" style={{ fontSize: 10.5, color: 'var(--text-quiet)', marginTop: 8 }}>
                {d.ended_at ? new Date(d.ended_at).toLocaleString() : '—'}
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}