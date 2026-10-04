import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, ProjectSummary } from '../api/client'

const NAV = [
  { label: 'Dashboard', to: '/dashboard' },
  { label: 'Risks', to: '/risks' },
  { label: 'Charters', to: '/charters/suggest' },
  { label: 'Bugs', to: '/bugs' },
  { label: 'Regression', to: '/regression' },
  { label: 'Debriefs', to: '/debriefs' },
  { label: 'Settings', to: '/settings' },
]

function NavLink({ to, label, active }: { to: string; label: string; active: boolean }) {
  return (
    <Link to={to} aria-current={active ? 'page' : undefined} style={{
      display: 'flex', alignItems: 'center', gap: 12, padding: '9px 12px', borderRadius: 8,
      background: active ? 'var(--accent-bg)' : 'transparent',
      color: active ? 'var(--accent)' : 'var(--text-secondary)',
      fontSize: 13.5, fontWeight: 500,
    }}>
      {label}
    </Link>
  )
}

/** Switch project and stay on the same kind of page (a session page goes to the new project's dashboard). */
function ProjectSwitcher({ projectId }: { projectId: string }) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const location = useLocation()
  const navigate = useNavigate()
  useEffect(() => { api.listProjects().then(setProjects).catch(console.error) }, [projectId])

  function switchTo(id: string) {
    const rest = location.pathname.replace(/^\/projects\/[^/]+/, '')
    const page = NAV.find((n) => rest === n.to)?.to ?? '/dashboard'
    navigate(`/projects/${id}${page}`)
  }

  return (
    <div style={{ marginBottom: 22 }}>
      <label htmlFor="project-switcher" className="mono" style={{
        display: 'block', fontSize: 10, color: 'var(--text-quiet)', textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 6,
      }}>Project</label>
      <select id="project-switcher" value={projectId} onChange={(e) => switchTo(e.target.value)} style={{
        width: '100%', background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 8,
        color: 'var(--text)', padding: '8px 10px', fontSize: 13, fontFamily: 'var(--font-ui)',
      }}>
        {projects.length === 0 && <option value={projectId}>Loading…</option>}
        {projects.filter((p) => !p.archived_at || p.id === projectId).map((p) => <option key={p.id} value={p.id}>{p.name}{p.archived_at ? ' (archived)' : ''}</option>)}
      </select>
      <Link to="/projects" style={{ display: 'inline-block', fontSize: 12, color: 'var(--text-tertiary)', marginTop: 7 }}>
        All projects
      </Link>
    </div>
  )
}

export default function Sidebar() {
  const location = useLocation()
  const { projectId } = useParams()
  return (
    <div style={{
      width: 232, flexShrink: 0, background: 'var(--panel-alt)', borderRight: '1px solid var(--border)',
      display: 'flex', flexDirection: 'column', padding: '28px 20px',
    }}>
      <Link to="/projects" title="Mirmera (ምርመራ): investigation" aria-label="Mirmera home" style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 32, color: 'inherit' }}>
        <img src="/mirmera.svg" width={32} height={32} alt="" style={{ display: 'block', flexShrink: 0 }} />
        <div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>AddisVerify</div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Mirmera</div>
        </div>
      </Link>
      {projectId ? (
        <>
          <ProjectSwitcher projectId={projectId} />
          <nav aria-label="Project pages" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {NAV.map((item) => {
              const to = `/projects/${projectId}${item.to}`
              return <NavLink key={item.to} to={to} label={item.label} active={location.pathname === to} />
            })}
          </nav>
        </>
      ) : (
        <nav aria-label="Main" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <NavLink to="/projects" label="Projects" active={location.pathname === '/projects'} />
        </nav>
      )}
    </div>
  )
}
