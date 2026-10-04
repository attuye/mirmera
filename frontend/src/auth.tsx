/** Who is signed in, and their workspaces. Pages under <RequireAuth> only render once signed in;
 *  when the API answers 401 (signed out elsewhere, session expired) the sign-in page comes back. */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { api, Me, SIGNED_OUT, WorkspaceRef } from './api/client'

interface AuthValue {
  me: Me | null
  loading: boolean
  setMe: (me: Me | null) => void
  signOut: () => Promise<void>
  /** The workspace picked on the Projects page (remembered in this browser). */
  workspace: WorkspaceRef | null
  setWorkspace: (id: string) => void
}

const AuthContext = createContext<AuthValue | null>(null)
const WORKSPACE_KEY = 'mirmera.workspace'

function remembered(): string | null {
  try { return localStorage.getItem(WORKSPACE_KEY) } catch { return null }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  const [workspaceId, setWorkspaceId] = useState<string | null>(remembered)

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null)).finally(() => setLoading(false))
    const out = () => setMe(null)
    window.addEventListener(SIGNED_OUT, out)
    return () => window.removeEventListener(SIGNED_OUT, out)
  }, [])

  const setWorkspace = useCallback((id: string) => {
    setWorkspaceId(id)
    try { localStorage.setItem(WORKSPACE_KEY, id) } catch { /* not remembered */ }
  }, [])
  const signOut = useCallback(async () => {
    try { await api.signOut() } finally { setMe(null) }
  }, [])
  const workspace = me ? (me.workspaces.find((w) => w.id === workspaceId) ?? me.workspaces[0] ?? null) : null

  return <AuthContext.Provider value={{ me, loading, setMe, signOut, workspace, setWorkspace }}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth() needs <AuthProvider>')
  return value
}

/** The role the signed-in user has in a workspace, or null. */
export function useRole(workspaceId: string | undefined): WorkspaceRef['role'] | null {
  const { me } = useAuth()
  return me?.workspaces.find((w) => w.id === workspaceId)?.role ?? null
}

export function RequireAuth() {
  const { me, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div style={{ padding: '80px 40px', textAlign: 'center', color: 'var(--text-secondary)' }}>Loading…</div>
  if (!me) return <Navigate to={`/signin?next=${encodeURIComponent(location.pathname + location.search)}`} replace />
  return <Outlet />
}
