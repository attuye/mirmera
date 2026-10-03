/** Projects home: every project with a few counts, and a form to start a new one. */
import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import { utcDate } from '../components/Evidence'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from '../components/ui'
import { api, ProjectSummary } from '../api/client'

function ago(iso: string): string {
  const minutes = Math.round((Date.now() - utcDate(iso).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  return days < 30 ? `${days} d ago` : utcDate(iso).toLocaleDateString()
}

function NewProjectForm({ onCancel }: { onCancel?: () => void }) {
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [appUrl, setAppUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function create(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const p = await api.createProject({
        name: name.trim(), description: description.trim() || undefined, app_url: appUrl.trim() || undefined,
      })
      navigate(`/projects/${p.id}/dashboard`)
    } catch (err) {
      setError(errorText(err))
      setBusy(false)
    }
  }

  return (
    <form aria-label="New project" onSubmit={create} style={{
      background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, padding: '20px 22px', maxWidth: 560,
    }}>
      <div style={{ fontSize: 14.5, fontWeight: 600, marginBottom: 14 }}>New project</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div>
          <label htmlFor="project-name" style={fieldLabel}>Name</label>
          <input id="project-name" value={name} onChange={(e) => setName(e.target.value)} style={input}
            placeholder="e.g. AddisVerify KYC" autoFocus maxLength={120} />
        </div>
        <div>
          <label htmlFor="project-description" style={fieldLabel}>Description (optional)</label>
          <input id="project-description" value={description} onChange={(e) => setDescription(e.target.value)} style={input}
            placeholder="One line: what the app does" maxLength={300} />
        </div>
        <div>
          <label htmlFor="project-url" style={fieldLabel}>App URL (optional)</label>
          <input id="project-url" value={appUrl} onChange={(e) => setAppUrl(e.target.value)} style={input}
            placeholder="http://localhost:4000" />
          <div style={{ fontSize: 11.5, color: 'var(--text-quiet)', marginTop: 5 }}>
            Where the app under test runs. Used by the recorded browser, the reproduction agent and regression runs.
            After creating the project, add more environments (staging, production…) under <b>Settings</b> in the sidebar.
          </div>
        </div>
      </div>
      <ErrorLine message={error} />
      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <button type="submit" disabled={busy || !name.trim()} style={primaryButton(busy || !name.trim())}>
          {busy ? 'Creating…' : 'Create project'}
        </button>
        {onCancel && <button type="button" onClick={onCancel} style={quietButton}>Cancel</button>}
      </div>
    </form>
  )
}

function Count({ n, one, many }: { n: number; one: string; many: string }) {
  return <span><span style={{ color: 'var(--text)' }}>{n}</span> {n === 1 ? one : many}</span>
}

function ProjectCard({ p }: { p: ProjectSummary }) {
  return (
    <Link to={`/projects/${p.id}/dashboard`} aria-label={p.name} style={{
      display: 'block', background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12,
      padding: '18px 20px', color: 'inherit',
    }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>{p.name}</span>
        {p.archived_at && <span className="mono" style={{ fontSize: 9.5, color: 'var(--text-tertiary)', border: '1px solid var(--border)', padding: '1px 6px', borderRadius: 5 }}>ARCHIVED</span>}
      </div>
      {p.description && <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.45 }}>{p.description}</div>}
      {p.app_url && <div className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 8, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.app_url}</div>}
      <div className="mono" style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', fontSize: 11, color: 'var(--text-tertiary)', marginTop: 14 }}>
        <Count n={p.risk_count} one="risk" many="risks" />
        <Count n={p.charter_count} one="charter" many="charters" />
        <Count n={p.session_count} one="session" many="sessions" />
        <Count n={p.bug_count} one="bug" many="bugs" />
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--text-quiet)', marginTop: 10 }}>
        {p.last_activity ? `Last session ${ago(p.last_activity)}` : `Created ${ago(p.created_at)}`}
      </div>
    </Link>
  )
}

export default function Projects() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const notice = (useLocation().state as { notice?: string } | null)?.notice

  useEffect(() => { api.listProjects().then(setProjects).catch((e) => setError(errorText(e))) }, [])

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24, maxWidth: 1000 }}>
          <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
            <img src="/mirmera.svg" width={48} height={48} alt="" style={{ display: 'block', flexShrink: 0 }} />
            <div>
            <h1 style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>Projects</h1>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>
              One per app you test. Each has its own risks, charters, sessions, bugs and settings.
            </div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)', marginTop: 6 }}>
              Mirmera · ምርመራ · investigation
            </div>
            </div>
          </div>
          {projects && projects.length > 0 && !creating && (
            <button onClick={() => setCreating(true)} style={{ ...primaryButton(), fontSize: 13.5, padding: '11px 18px' }}>+ New project</button>
          )}
        </div>
        {notice && <div role="status" style={{ fontSize: 12.5, color: 'var(--accent)', marginBottom: 14 }}>{notice}</div>}
        <ErrorLine message={error} />
        {!projects && !error && <div style={{ color: 'var(--text-secondary)' }}>Loading…</div>}
        {projects && projects.length === 0 && (
          <>
            <p style={{ fontSize: 13.5, color: 'var(--text-secondary)', margin: '0 0 16px' }}>No projects yet. Create one to start testing.</p>
            <NewProjectForm />
          </>
        )}
        {creating && <div style={{ marginBottom: 24 }}><NewProjectForm onCancel={() => setCreating(false)} /></div>}
        {projects && projects.length > 0 && (() => {
          const archived = projects.filter((p) => p.archived_at).length
          const shown = projects.filter((p) => showArchived || !p.archived_at)
          return (
            <>
              {shown.length === 0 && <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Every project is archived.</p>}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14, maxWidth: 1000 }}>
                {shown.map((p) => <ProjectCard key={p.id} p={p} />)}
              </div>
              {archived > 0 && (
                <label style={{ display: 'flex', gap: 7, alignItems: 'center', fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 16 }}>
                  <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
                  Show archived ({archived})
                </label>
              )}
            </>
          )
        })()}
      </main>
    </div>
  )
}
