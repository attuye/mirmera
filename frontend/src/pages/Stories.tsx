/** User stories: import one from Jira or GitHub, or write one here. Each story links to
 *  risks and charters; its page shows the testing under them and what is still left. */
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Bot } from 'lucide-react'
import Sidebar from '../components/Sidebar'
import { useAgentActivity } from '../components/AgentProgress'
import { ErrorLine, errorText, fieldLabel, input, primaryButton } from '../components/ui'
import { AgentActivity, api, SprintSummary, StorySummary } from '../api/client'
import { useProject, useProjectPath } from '../project'

type Mode = 'import' | 'write'
type Show = 'open' | 'done' | 'all'

export const timeText = (minutes: number) =>
  minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h${minutes % 60 ? ` ${minutes % 60} min` : ''}`

export function StoryStatus({ story }: { story: Pick<StorySummary, 'closed' | 'tracker_status'> }) {
  const text = story.tracker_status ?? (story.closed ? 'Done' : 'Open')
  return (
    <span className="mono" style={{
      fontSize: 9.5, padding: '3px 7px', borderRadius: 5, whiteSpace: 'nowrap', textTransform: 'uppercase',
      background: story.closed ? 'var(--panel)' : 'var(--accent-bg)', color: story.closed ? 'var(--text-tertiary)' : 'var(--accent)',
      border: story.closed ? '1px solid var(--border)' : '1px solid transparent',
    }}>{text}</span>
  )
}

function AddStory({ onAdded }: { onAdded: (id: string, note?: string | null) => void }) {
  const { project } = useProject()
  const [mode, setMode] = useState<Mode>(project.jira_project_key || project.github_repo ? 'import' : 'write')
  const [ref, setRef] = useState('')
  const [form, setForm] = useState({ title: '', key: '', description: '', url: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run(fn: () => Promise<{ id: string; sprint_note?: string | null }>) {
    setBusy(true); setError(null)
    try { const s = await fn(); onAdded(s.id, s.sprint_note) } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const tab = (m: Mode, label: string) => (
    <button key={m} role="tab" aria-selected={mode === m} onClick={() => { setMode(m); setError(null) }} style={{
      border: 'none', padding: '6px 14px', fontSize: 12.5, cursor: 'pointer', fontFamily: 'var(--font-ui)',
      background: mode === m ? 'var(--accent-bg)' : 'transparent', color: mode === m ? 'var(--accent)' : 'var(--text-secondary)',
    }}>{label}</button>
  )
  const where = [project.jira_project_key && `Jira (${project.jira_project_key})`, project.github_repo && `GitHub (${project.github_repo})`].filter(Boolean).join(' or ')

  return (
    <section aria-label="Add a story" style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 16, maxWidth: 760, marginTop: 20 }}>
      <div role="tablist" aria-label="Add a story" style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', marginBottom: 12 }}>
        {tab('import', 'From Jira or GitHub')}{tab('write', 'Write one')}
      </div>
      {mode === 'import' ? (
        <form onSubmit={(e) => { e.preventDefault(); if (ref.trim()) run(() => api.importStory(project.id, ref.trim())) }}
          style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <label htmlFor="story-ref" style={fieldLabel}>Story key or address</label>
            <input id="story-ref" value={ref} onChange={(e) => setRef(e.target.value)} style={input}
              placeholder={project.jira_project_key ? `e.g. ${project.jira_project_key}-12, or #45 for GitHub` : 'e.g. KYC-12 or #45'} />
          </div>
          <button type="submit" disabled={busy || !ref.trim()} style={primaryButton(busy || !ref.trim())}>{busy ? 'Importing…' : 'Import story'}</button>
        </form>
      ) : (
        <form onSubmit={(e) => {
          e.preventDefault()
          if (form.title.trim().length >= 3) run(() => api.createStory(project.id, form))
        }} style={{ display: 'grid', gridTemplateColumns: '140px 1fr', gap: 10 }}>
          <div>
            <label htmlFor="story-key" style={fieldLabel}>Key (optional)</label>
            <input id="story-key" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} style={input} placeholder="KYC-12" />
          </div>
          <div>
            <label htmlFor="story-title" style={fieldLabel}>Story</label>
            <input id="story-title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} style={input}
              placeholder="e.g. As a customer I can upload my passport" />
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <label htmlFor="story-description" style={fieldLabel}>What it claims (description and acceptance criteria)</label>
            <textarea id="story-description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
              style={{ ...input, height: 90, resize: 'vertical' }} />
          </div>
          <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 8, alignItems: 'flex-end' }}>
            <div style={{ flex: 1 }}>
              <label htmlFor="story-url" style={fieldLabel}>Address (optional)</label>
              <input id="story-url" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} style={input} placeholder="https://…" />
            </div>
            <button type="submit" disabled={busy || form.title.trim().length < 3} style={primaryButton(busy || form.title.trim().length < 3)}>
              {busy ? 'Adding…' : 'Add story'}
            </button>
          </div>
        </form>
      )}
      {mode === 'import' && (
        <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 6 }}>
          {where ? `Reads the title, description and status from ${where}.` : 'Set the Jira project key or GitHub repo in Settings first, or write the story here.'}
        </div>
      )}
      <ErrorLine message={error} />
    </section>
  )
}

/** What Mirmera is doing on a story right now, in a few words. */
export function agentDoing(item: AgentActivity): string {
  const ex = item.exploration
  if (ex && item.stage === 'explore') return `Exploring in the app · step ${ex.steps_used} of ${ex.max_steps}`
  const active = item.stages.find((s) => s.status === 'active')
  return active ? active.label : 'Working'
}

function MirmeraWorking({ item }: { item: AgentActivity }) {
  return (
    <span role="status" aria-label="Mirmera is working on this story" style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--info)', whiteSpace: 'nowrap',
      border: '1px solid var(--border)', borderRadius: 6, padding: '2px 8px', background: 'var(--panel)',
    }}>
      <Bot size={12} aria-hidden className="mm-pulse" />Mirmera: {agentDoing(item)}
    </span>
  )
}

export default function Stories() {
  const { project } = useProject()
  const path = useProjectPath()
  const navigate = useNavigate()
  const [stories, setStories] = useState<StorySummary[] | null>(null)
  const [show, setShow] = useState<Show>('open')
  const [sprints, setSprints] = useState<SprintSummary[]>([])
  const [sprint, setSprint] = useState('')  // '' every sprint, 'none' no sprint, or a sprint id
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listStories(project.id).then(setStories).catch((e) => setError(errorText(e)))
    api.listSprints(project.id).then(setSprints).catch(() => {})
  }, [project.id])
  // Mirmera's work in this workspace (started from Jira or here): which stories it is on right now.
  const activity = useAgentActivity(() => api.workspaceActivity(project.org_id), `stories-${project.org_id}`, 5000)
  const working = new Map((activity?.items ?? []).filter((i) => i.state === 'running' && i.story_id).map((i) => [i.story_id!, i]))
  const workingOn = [...working.keys()].sort().join(',')
  const wasWorkingOn = useRef('')
  useEffect(() => {  // it started on a story (maybe a new one from Jira) or finished one: the list changed
    if (workingOn !== wasWorkingOn.current) api.listStories(project.id).then(setStories).catch(() => {})
    wasWorkingOn.current = workingOn
  }, [workingOn, project.id])
  const inSprint = (s: StorySummary) => !sprint || (sprint === 'none' ? !s.sprint : s.sprint?.id === sprint)
  const shown = (stories ?? []).filter((s) => inSprint(s) && (show === 'all' || (show === 'done') === s.closed))
  const count = (which: Show) => (stories ?? []).filter((s) => inSprint(s) && (which === 'all' || (which === 'done') === s.closed)).length

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1100 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Stories</h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 720, lineHeight: 1.5 }}>
          A story is a claim about a change, one oracle among several. Link the risks it raises and the charters that explore
          it; the risks stay in the project after the story closes. Each story shows the testing done under it and what is
          still left, never a tick that says "tested".
        </p>

        <AddStory onAdded={(id, note) => navigate(path(`/stories/${id}`), { state: note ? { sprintNote: note } : undefined })} />
        <ErrorLine message={error} />

        <section aria-label="Your stories" style={{ marginTop: 26, maxWidth: 900 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Your stories</h2>
            <div role="group" aria-label="Show" style={{ display: 'flex', gap: 4 }}>
              {(['open', 'done', 'all'] as Show[]).map((s) => (
                <button key={s} aria-pressed={show === s} onClick={() => setShow(s)} style={{
                  fontSize: 12, padding: '4px 10px', borderRadius: 6, cursor: 'pointer', fontFamily: 'var(--font-ui)',
                  border: `1px solid ${show === s ? 'var(--accent)' : 'var(--border)'}`, background: 'transparent',
                  color: show === s ? 'var(--accent)' : 'var(--text-secondary)',
                }}>{s === 'open' ? 'Open' : s === 'done' ? 'Done' : 'All'} {count(s)}</button>
              ))}
            </div>
            {sprints.length > 0 && (
              <select aria-label="Sprint" value={sprint} onChange={(e) => setSprint(e.target.value)} style={{ ...input, width: 'auto', fontSize: 12, padding: '4px 8px' }}>
                <option value="">Every sprint</option>
                {sprints.map((sp) => <option key={sp.id} value={sp.id}>{sp.name}{sp.state === 'current' ? ' (current)' : ''}</option>)}
                <option value="none">No sprint</option>
              </select>
            )}
          </div>
          {stories && shown.length === 0 && (
            <p style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>
              {stories.length === 0 ? 'No stories yet. Import one from your tracker, or write one above.' : 'None here.'}
            </p>
          )}
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {shown.map((s) => (
              <li key={s.id} aria-label={s.title} style={{ border: '1px solid var(--border)', borderRadius: 9, padding: '11px 14px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  {s.key && <span className="mono" style={{ fontSize: 11.5, color: 'var(--text-tertiary)', flexShrink: 0 }}>{s.key}</span>}
                  <Link to={path(`/stories/${s.id}`)} style={{ fontSize: 13.5, fontWeight: 500, color: 'var(--text)', flex: 1, minWidth: 0 }}>{s.title}</Link>
                  {working.has(s.id) && <MirmeraWorking item={working.get(s.id)!} />}
                  <StoryStatus story={s} />
                </div>
                <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 6, fontSize: 12, color: 'var(--text-tertiary)' }}>
                  <span>{s.risks} risk{s.risks === 1 ? '' : 's'}</span>
                  <span>{s.charters} charter{s.charters === 1 ? '' : 's'}</span>
                  <span>{s.sessions} session{s.sessions === 1 ? '' : 's'}{s.minutes ? `, ${timeText(s.minutes)}` : ''}</span>
                  {s.sprint && <Link to={path(`/sprints/${s.sprint.id}`)} style={{ color: 'var(--info)' }}>{s.sprint.name}</Link>}
                  <span>{s.bugs} bug{s.bugs === 1 ? '' : 's'}{s.high_bugs ? <span style={{ color: 'var(--danger)' }}> ({s.high_bugs} high)</span> : ''}</span>
                  <span style={{ marginLeft: 'auto', color: s.left ? 'var(--warning)' : 'var(--text-tertiary)' }}>
                    {s.left ? `${s.left} thing${s.left === 1 ? '' : 's'} left to look at` : 'Nothing obvious left'}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  )
}
