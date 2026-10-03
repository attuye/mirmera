import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import TesterField, { useTesterName } from '../components/TesterField'
import { ErrorLine, errorText } from '../components/ui'
import { api, Charter } from '../api/client'
import { useProject, useProjectPath } from '../project'

const PRIORITY_COLOR: Record<string, string> = { high: 'var(--danger)', medium: 'var(--warning)', low: 'var(--text-tertiary)' }
const STATUS_STYLE: Record<string, { bg: string; fg: string }> = {
  active: { bg: 'var(--accent-bg)', fg: 'var(--accent)' },
  proposed: { bg: 'var(--warning-bg)', fg: 'var(--warning)' },
  done: { bg: '#1A1D22', fg: 'var(--text-secondary)' },
}

export default function Dashboard() {
  const { project } = useProject()
  const path = useProjectPath()
  const [charters, setCharters] = useState<Charter[]>([])
  const [loading, setLoading] = useState(true)
  const [tester, setTester] = useTesterName(project)
  const [error, setError] = useState<string | null>(null)

  const navigate = useNavigate()

  async function openCharter(c: Charter) {
    setError(null)
    if (c.status === 'active') {
      try {
        const session = await api.getActiveSession(c.id)
        navigate(path(`/sessions/${session.id}`))
      } catch {
        console.warn('Charter marked active but no open session found')
      }
      return
    }
    if (c.status === 'proposed') {
      if (!tester.trim()) { setError('Enter your name under "Testing as" first.'); return }
      try {
        const session = await api.startSession(c.id, tester.trim())
        navigate(path(`/sessions/${session.id}`))
      } catch (e) {
        setError(errorText(e))
      }
    }
  }

  useEffect(() => {
    setLoading(true)
    api.listCharters(project.id).then(setCharters).catch((e) => setError(errorText(e))).finally(() => setLoading(false))
  }, [project.id])

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <div style={{ flex: 1, padding: '32px 40px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 28 }}>
          <div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)', textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 6 }}>Project</div>
            <h1 style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>{project.name}</h1>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>{project.description || 'Context-driven exploratory testing workspace'}</div>
            {project.app_url && <div className="mono" style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 6 }}>{project.app_url}</div>}
          </div>
          <Link to={path('/charters/suggest')} style={{
            background: 'var(--accent)', color: '#0C0D0F', fontSize: 13.5, fontWeight: 600,
            padding: '11px 18px', borderRadius: 8,
          }}>+ New Session</Link>
        </div>

        {loading ? (
          <div style={{ color: 'var(--text-secondary)' }}>Loading…</div>
        ) : (
          <div style={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, padding: '20px 22px', maxWidth: 700 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 14 }}>
              <div style={{ fontSize: 14.5, fontWeight: 600 }}>Charters</div>
              <TesterField value={tester} onChange={setTester} />
            </div>
            <ErrorLine message={error} />
            {charters.length === 0 && (
              <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                No charters yet. Start with <Link to={path('/risks')}>Risks</Link> to plan charters from what could go wrong,
                or <Link to={path('/charters/suggest')}>generate some from a diff</Link>.
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {charters.map((c) => {
                const status = STATUS_STYLE[c.status] ?? STATUS_STYLE.proposed
                return (
                  // <div key={c.id} style={{ border: '1px solid var(--border)', borderRadius: 9, padding: '13px 14px' }}>
                  <div
                    key={c.id}
                    onClick={() => openCharter(c)}
                    style={{
                      border: '1px solid var(--border)', borderRadius: 9, padding: '13px 14px',
                      cursor: c.status === 'active' || c.status === 'proposed' ? 'pointer' : 'default',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                      <div style={{ fontSize: 13, fontWeight: 500 }}>{c.mission_statement}</div>
                      <div className="mono" style={{ fontSize: 9.5, background: status.bg, color: status.fg, padding: '3px 7px', borderRadius: 5, whiteSpace: 'nowrap' }}>
                        {c.status.toUpperCase()}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 9, alignItems: 'center' }}>
                      <span className="mono" style={{ fontSize: 9.5, color: 'var(--question)', background: '#221C2B', padding: '3px 7px', borderRadius: 5 }}>
                        {c.source === 'ai-suggested' ? 'AI-suggested' : 'Manual'}
                      </span>
                      <span className="mono" style={{ fontSize: 9.5, color: PRIORITY_COLOR[c.priority] }}>{c.priority} priority</span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
