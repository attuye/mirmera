/** The current project: loaded once by the /projects/:projectId route and
 *  shared with every page under it. */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { Link, Navigate, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, Project } from './api/client'
import { errorText } from './components/ui'

interface ProjectContextValue {
  project: Project
  /** Call after saving project settings so every page sees the change. */
  setProject: (p: Project) => void
}

const ProjectContext = createContext<ProjectContextValue | null>(null)

const LAST_PROJECT_KEY = 'mirmera.lastProject'

/** The project last opened in this browser, so "/" can go straight back to it. */
export function lastProjectId(): string | null {
  try { return localStorage.getItem(LAST_PROJECT_KEY) } catch { return null }
}

function rememberProject(id: string | null) {
  try { id ? localStorage.setItem(LAST_PROJECT_KEY, id) : localStorage.removeItem(LAST_PROJECT_KEY) } catch { /* storage blocked */ }
}

export function useProject(): ProjectContextValue {
  const value = useContext(ProjectContext)
  if (!value) throw new Error('useProject() is only available on pages under /projects/:projectId')
  return value
}

/** Builds links inside the current project: path('/risks') -> /projects/<id>/risks */
export function useProjectPath(): (path: string) => string {
  const { projectId } = useParams()
  return useCallback((path: string) => `/projects/${projectId}${path}`, [projectId])
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: '80px 40px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>{children}</div>
}

export function ProjectLayout() {
  const { projectId } = useParams()
  const [project, setProject] = useState<Project | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [missing, setMissing] = useState(false)

  useEffect(() => {
    if (!projectId) return
    setProject(null); setError(null); setMissing(false)
    api.getProject(projectId)
      .then((p) => { setProject(p); rememberProject(p.id) })
      .catch((e: Error) => {
        if (e.message === 'Project not found') { setMissing(true); rememberProject(null) } else setError(errorText(e))
      })
  }, [projectId])

  if (missing) {
    return <Centered>This project doesn't exist (it may be from another database). <Link to="/projects">See all projects</Link></Centered>
  }
  if (error) return <Centered><span role="alert" style={{ color: 'var(--danger)' }}>{error}</span></Centered>
  if (!project) return <Centered>Loading project…</Centered>
  return (
    <ProjectContext.Provider value={{ project, setProject }}>
      <Outlet />
    </ProjectContext.Provider>
  )
}

/** Old links (/sessions/<id>, from before projects) find their project and move there. */
export function SessionRedirect({ debrief = false }: { debrief?: boolean }) {
  const { sessionId } = useParams()
  const [target, setTarget] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!sessionId) return
    api.getSession(sessionId)
      .then((s) => setTarget(`/projects/${s.project_id}/sessions/${s.id}${debrief ? '/debrief' : ''}`))
      .catch((e) => setError(errorText(e)))
  }, [sessionId, debrief])
  if (error) return <Centered><span role="alert" style={{ color: 'var(--danger)' }}>{error}</span> <Link to="/projects">See all projects</Link></Centered>
  return target ? <Navigate to={target} replace /> : <Centered>Opening session…</Centered>
}

/** A session opened under the wrong project (an edited URL, an old link) moves to its own project. */
export function useSessionProject(sessionProjectId: string | null | undefined) {
  const { project } = useProject()
  const location = useLocation()
  const navigate = useNavigate()
  useEffect(() => {
    if (sessionProjectId && sessionProjectId !== project.id) {
      navigate(location.pathname.replace(/^\/projects\/[^/]+/, `/projects/${sessionProjectId}`), { replace: true })
    }
  }, [sessionProjectId, project.id, location.pathname, navigate])
}
