import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft, BookOpen, CalendarRange, Bug, ChevronRight, Compass, FileText, FlaskConical, FolderKanban, LayoutDashboard, Lightbulb, Map as MapIcon,
  MessageSquareText, ScanSearch, ScrollText, Settings, ShieldAlert, StickyNote, TrendingUp, type LucideIcon,
} from 'lucide-react'
import { api, ProjectSummary } from '../api/client'

type Item = { label: string; to: string; icon: LucideIcon }
type Group = { label: string; icon: LucideIcon; items: Item[] }
type Entry = Item | Group

/** The project's pages, in the order testing goes: plan, test, find, learn. */
const MENU: Entry[] = [
  { label: 'Dashboard', to: '/dashboard', icon: LayoutDashboard },
  { label: 'Plan', icon: Compass, items: [
    { label: 'Sprints', to: '/sprints', icon: CalendarRange },
    { label: 'Stories', to: '/stories', icon: StickyNote },
    { label: 'Risks', to: '/risks', icon: ShieldAlert },
    { label: 'Charters', to: '/charters/suggest', icon: ScrollText },
    { label: 'Heuristics', to: '/heuristics', icon: BookOpen },
  ] },
  { label: 'Findings', icon: ScanSearch, items: [
    { label: 'Bugs', to: '/bugs', icon: Bug },
    { label: 'Regression', to: '/regression', icon: FlaskConical },
  ] },
  { label: 'Insights', icon: Lightbulb, items: [
    { label: 'Debriefs', to: '/debriefs', icon: MessageSquareText },
    { label: 'Coverage', to: '/coverage', icon: MapIcon },
    { label: 'Trends', to: '/trends', icon: TrendingUp },
    { label: 'Report', to: '/report', icon: FileText },
  ] },
  { label: 'Settings', to: '/settings', icon: Settings },
]
const isGroup = (e: Entry): e is Group => 'items' in e
const NAV: Item[] = MENU.flatMap((e) => (isGroup(e) ? e.items : [e]))

/** Which page a path belongs to: a session counts as the Dashboard (sessions start there), its debrief as Debriefs. */
function activePage(rest: string): string | undefined {
  if (/^\/sessions\/[^/]+\/debrief$/.test(rest)) return '/debriefs'
  if (rest.startsWith('/sessions/')) return '/dashboard'
  return NAV.find((n) => rest === n.to || rest.startsWith(n.to + '/'))?.to
}

const OPEN_KEY = 'mirmera.menu.closed'
function useClosedGroups(): [Set<string>, (label: string) => void] {
  const [closed, setClosed] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem(OPEN_KEY) ?? '[]')) } catch { return new Set() }
  })
  function toggle(label: string) {
    setClosed((prev) => {
      const next = new Set(prev)
      if (next.has(label)) next.delete(label); else next.add(label)
      try { localStorage.setItem(OPEN_KEY, JSON.stringify([...next])) } catch { /* private mode: not remembered */ }
      return next
    })
  }
  return [closed, toggle]
}

const ICON = { size: 16, strokeWidth: 1.8 }

function NavLink({ to, label, icon: Icon, active, sub }: { to: string; label: string; icon: LucideIcon; active: boolean; sub?: boolean }) {
  return (
    <Link to={to} aria-current={active ? 'page' : undefined} style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: sub ? '7px 12px 7px 34px' : '9px 12px', borderRadius: 8,
      background: active ? 'var(--accent-bg)' : 'transparent',
      color: active ? 'var(--accent)' : 'var(--text-secondary)',
      fontSize: sub ? 13 : 13.5, fontWeight: 500,
    }}>
      <Icon {...ICON} size={sub ? 15 : 16} aria-hidden style={{ flexShrink: 0 }} />
      {label}
    </Link>
  )
}

function NavGroup({ group, base, current, open, onToggle }: {
  group: Group; base: string; current: string | undefined; open: boolean; onToggle: () => void
}) {
  const { icon: Icon } = group
  const holdsActive = group.items.some((i) => i.to === current)
  const id = `menu-${group.label.toLowerCase()}`
  return (
    <div>
      <button onClick={onToggle} aria-expanded={open} aria-controls={id} style={{
        display: 'flex', alignItems: 'center', gap: 10, width: '100%', padding: '9px 12px', borderRadius: 8,
        background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left',
        color: holdsActive && !open ? 'var(--accent)' : 'var(--text-secondary)', fontSize: 13.5, fontWeight: 500,
        fontFamily: 'var(--font-ui)',
      }}>
        <Icon {...ICON} aria-hidden style={{ flexShrink: 0 }} />
        <span style={{ flex: 1 }}>{group.label}</span>
        <ChevronRight size={14} strokeWidth={2} aria-hidden style={{
          color: 'var(--text-quiet)', transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 120ms ease',
        }} />
      </button>
      {open && (
        <div id={id} role="group" aria-label={group.label} style={{ display: 'flex', flexDirection: 'column', gap: 1, marginBottom: 2 }}>
          {group.items.map((item) => (
            <NavLink key={item.to} to={`${base}${item.to}`} label={item.label} icon={item.icon} active={current === item.to} sub />
          ))}
        </div>
      )}
    </div>
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
    const page = rest.startsWith('/sessions/') ? '/dashboard' : activePage(rest) ?? '/dashboard'
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
      <Link to="/projects" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--text-tertiary)', marginTop: 7 }}>
        <ArrowLeft size={13} strokeWidth={1.8} aria-hidden />All projects
      </Link>
    </div>
  )
}

export default function Sidebar() {
  const location = useLocation()
  const { projectId } = useParams()
  const [closed, toggle] = useClosedGroups()
  const base = `/projects/${projectId}`
  const current = activePage(location.pathname.replace(/^\/projects\/[^/]+/, ''))
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
            {MENU.map((entry) => isGroup(entry) ? (
              <NavGroup key={entry.label} group={entry} base={base} current={current}
                open={!closed.has(entry.label)} onToggle={() => toggle(entry.label)} />
            ) : (
              <NavLink key={entry.to} to={`${base}${entry.to}`} label={entry.label} icon={entry.icon} active={current === entry.to} />
            ))}
          </nav>
        </>
      ) : (
        <nav aria-label="Main" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <NavLink to="/projects" label="Projects" icon={FolderKanban} active={location.pathname === '/projects'} />
        </nav>
      )}
    </div>
  )
}
