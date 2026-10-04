/** One sprint: its stories and what's left in them, everything tested in its dates (under
 *  its stories and outside them), and high risks nobody explored in those dates. */
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, FileText, Map as MapIcon } from 'lucide-react'
import Sidebar from '../components/Sidebar'
import StoryChips from '../components/StoryChips'
import { ConfirmDelete, ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from '../components/ui'
import { api, SprintDetail, SprintPart, StorySummary } from '../api/client'
import { useProject, useProjectPath } from '../project'
import { StoryStatus, timeText } from './Stories'
import { SprintState, sprintDates } from './Sprints'

const small: React.CSSProperties = { ...quietButton, fontSize: 12, padding: '4px 10px' }
const quiet: React.CSSProperties = { fontSize: 12.5, color: 'var(--text-tertiary)', margin: 0 }
const plural = (n: number, word: string, many = `${word}s`) => `${n} ${n === 1 ? word : many}`

function Section({ title, count, aside, children }: { title: string; count?: number; aside?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section aria-label={title} style={{ marginTop: 26 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
        <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
          {title} {count !== undefined && <span style={{ fontWeight: 400, color: 'var(--text-tertiary)', fontSize: 13 }}>{count}</span>}
        </h2>
        {aside && <div style={{ marginLeft: 'auto' }}>{aside}</div>}
      </div>
      {children}
    </section>
  )
}

/** Testing time in the sprint's dates: under its stories, and outside them. */
function TimeSplit({ under, outside }: { under: SprintPart; outside: SprintPart }) {
  const total = under.minutes + outside.minutes
  const share = total ? Math.round((under.minutes / total) * 100) : 0
  const part = (label: string, p: SprintPart, color: string) => (
    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 12.5 }}>
      <span aria-hidden style={{ width: 10, height: 10, borderRadius: 2, background: color, display: 'inline-block' }} />
      <span style={{ minWidth: 150 }}>{label}</span>
      <span style={{ color: 'var(--text-secondary)' }}>{plural(p.sessions, 'session')}, {timeText(p.minutes)}, {plural(p.bugs, 'bug')}</span>
    </div>
  )
  return (
    <div role="group" aria-label="Where the time went" style={{ maxWidth: 640 }}>
      {total > 0 && (
        <div aria-hidden style={{ display: 'flex', height: 10, borderRadius: 4, overflow: 'hidden', gap: 2, marginBottom: 10 }}>
          {under.minutes > 0 && <div style={{ width: `${share}%`, background: 'var(--accent)' }} />}
          {outside.minutes > 0 && <div style={{ flex: 1, background: 'var(--info)' }} />}
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {part("Under the sprint's stories", under, 'var(--accent)')}
        {part('Outside them', outside, 'var(--info)')}
      </div>
    </div>
  )
}

function EditSprint({ sprint, onSaved, onCancel }: { sprint: SprintDetail; onSaved: (s: SprintDetail) => void; onCancel: () => void }) {
  const [form, setForm] = useState({ name: sprint.name, goal: sprint.goal ?? '', starts_on: sprint.starts_on, ends_on: sprint.ends_on })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <form aria-label="Edit sprint" onSubmit={async (e) => {
      e.preventDefault(); setBusy(true); setError(null)
      try { onSaved(await api.updateSprint(sprint.id, form)) } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
    }} style={{ display: 'grid', gridTemplateColumns: '1fr 150px 150px', gap: 10, marginTop: 14, maxWidth: 760 }}>
      <div><label htmlFor="edit-name" style={fieldLabel}>Name</label><input id="edit-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={input} /></div>
      <div><label htmlFor="edit-start" style={fieldLabel}>Starts</label><input id="edit-start" type="date" value={form.starts_on} onChange={(e) => setForm({ ...form, starts_on: e.target.value })} style={input} /></div>
      <div><label htmlFor="edit-end" style={fieldLabel}>Ends (included)</label><input id="edit-end" type="date" value={form.ends_on} onChange={(e) => setForm({ ...form, ends_on: e.target.value })} style={input} /></div>
      <div style={{ gridColumn: '1 / -1' }}><label htmlFor="edit-goal" style={fieldLabel}>Goal</label><input id="edit-goal" value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} style={input} /></div>
      <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 8 }}>
        <button type="submit" disabled={busy} style={primaryButton(busy)}>{busy ? 'Saving…' : 'Save sprint'}</button>
        <button type="button" onClick={onCancel} style={quietButton}>Cancel</button>
        <ErrorLine message={error} />
      </div>
    </form>
  )
}

export default function SprintPage() {
  const { sprintId = '' } = useParams()
  const { project } = useProject()
  const path = useProjectPath()
  const navigate = useNavigate()
  const [sprint, setSprint] = useState<SprintDetail | null>(null)
  const [stories, setStories] = useState<StorySummary[]>([])
  const [adding, setAdding] = useState('')
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getSprint(sprintId).then(setSprint).catch((e) => setError(errorText(e)))
    api.listStories(project.id).then(setStories).catch(() => {})
  }, [sprintId, project.id])

  async function setStoryIds(ids: string[]) {
    setError(null)
    try {
      setSprint(await api.setSprintStories(sprintId, ids))
      setStories(await api.listStories(project.id))
    } catch (e) { setError(errorText(e)) }
  }
  if (!sprint) return <div style={{ display: 'flex', minHeight: '100vh' }}><Sidebar /><main style={{ padding: '32px 40px' }}>{error ? <ErrorLine message={error} /> : 'Loading…'}</main></div>

  const ids = sprint.story_list.map((s) => s.id)
  const candidates = stories.filter((s) => !ids.includes(s.id) && !s.closed)
  const period = `since=${sprint.starts_on}&until=${sprint.ends_on}`

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '28px 40px 60px', maxWidth: 1000 }}>
        <Link to={path('/sprints')} style={{ fontSize: 12.5, color: 'var(--text-tertiary)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <ArrowLeft size={13} aria-hidden /> Sprints
        </Link>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginTop: 10 }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <span className="mono" style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{sprintDates(sprint)}</span>
              <SprintState sprint={sprint} />
            </div>
            <h1 style={{ fontSize: 22, fontWeight: 600, margin: '6px 0 0' }}>{sprint.name}</h1>
            {sprint.goal && <p style={{ fontSize: 13.5, color: 'var(--text-secondary)', margin: '4px 0 0' }}>{sprint.goal}</p>}
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <Link to={path(`/report?${period}`)} style={{ ...quietButton, display: 'inline-flex', gap: 6, alignItems: 'center' }}><FileText size={13} aria-hidden />Report</Link>
            <Link to={path(`/coverage?${period}`)} style={{ ...quietButton, display: 'inline-flex', gap: 6, alignItems: 'center' }}><MapIcon size={13} aria-hidden />Coverage</Link>
            <button onClick={() => setEditing(!editing)} style={quietButton}>Edit</button>
            <ConfirmDelete label="Delete sprint" question="Delete this sprint? Its stories stay, outside any sprint."
              onConfirm={async () => { await api.deleteSprint(sprint.id); navigate(path('/sprints')) }} />
          </div>
        </div>
        {editing && <EditSprint sprint={sprint} onSaved={(s) => { setSprint(s); setEditing(false) }} onCancel={() => setEditing(false)} />}
        <div role="group" aria-label="Totals" style={{ display: 'flex', gap: 22, marginTop: 14, fontSize: 12.5, color: 'var(--text-secondary)', flexWrap: 'wrap' }}>
          <span><b>{sprint.stories_done}</b> of <b>{sprint.stories}</b> stories done</span>
          <span><b>{sprint.sessions}</b> {sprint.sessions === 1 ? 'session' : 'sessions'}{sprint.minutes ? `, ${timeText(sprint.minutes)}` : ''}</span>
          <span><b>{sprint.bugs}</b> {sprint.bugs === 1 ? 'bug' : 'bugs'}</span>
        </div>
        <ErrorLine message={error} />

        <Section title="What's left" count={sprint.left_list.length}>
          {sprint.left_list.length === 0
            ? <p style={quiet}>Nothing obvious left. That isn't a verdict: decide whether this sprint's testing is enough for the people who matter.</p>
            : <ul aria-label="What's left" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6, maxWidth: 860 }}>
                {sprint.left_list.map((l, i) => (
                  <li key={i} style={{ display: 'flex', gap: 12, alignItems: 'center', fontSize: 13, padding: '7px 12px', borderLeft: '3px solid var(--warning)', background: 'var(--panel)', borderRadius: 6 }}>
                    <span style={{ flex: 1 }}>{l.text}</span>
                    <Link to={path(l.to)} style={small}>{l.action}</Link>
                  </li>
                ))}
              </ul>}
        </Section>

        <Section title="Stories" count={sprint.story_list.length} aside={candidates.length > 0 && (
          <span style={{ display: 'inline-flex', gap: 6 }}>
            <select aria-label="Add a story to this sprint" value={adding} onChange={(e) => setAdding(e.target.value)} style={{ ...input, width: 300, fontSize: 12, padding: '4px 6px' }}>
              <option value="">Add a story…</option>
              {candidates.map((s) => <option key={s.id} value={s.id}>{s.key ? `${s.key}: ` : ''}{s.title}{s.sprint ? ` (from ${s.sprint.name})` : ''}</option>)}
            </select>
            <button disabled={!adding} style={small} onClick={async () => { await setStoryIds([...ids, adding]); setAdding('') }}>Add</button>
          </span>
        )}>
          {sprint.story_list.length === 0 && <p style={quiet}>No stories planned yet. Add them here, or from the Stories page.</p>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {sprint.story_list.map((s) => (
              <div key={s.id} role="article" aria-label={s.title} style={{ border: '1px solid var(--border)', borderRadius: 9, padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                    {s.key && <span className="mono" style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{s.key}</span>}
                    <Link to={path(`/stories/${s.id}`)} style={{ fontSize: 13.5, color: 'var(--text)' }}>{s.title}</Link>
                    <StoryStatus story={s} />
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    <span>{plural(s.risks, 'risk')}</span><span>{plural(s.charters, 'charter')}</span>
                    <span>{plural(s.sessions, 'session')}{s.minutes ? `, ${timeText(s.minutes)}` : ''}</span><span>{plural(s.bugs, 'bug')}</span>
                    <span style={{ color: s.left ? 'var(--warning)' : 'var(--text-tertiary)' }}>{s.left ? `${s.left} left` : 'nothing obvious left'}</span>
                  </div>
                </div>
                <button onClick={() => setStoryIds(ids.filter((x) => x !== s.id))} style={small} aria-label={`Take ${s.key ?? s.title} out of the sprint`}>Take out</button>
              </div>
            ))}
          </div>
        </Section>

        <Section title="Testing in these dates">
          {sprint.sessions === 0 ? <p style={quiet}>No sessions in these dates yet.</p> : <TimeSplit under={sprint.under_stories} outside={sprint.outside_stories} />}
          {sprint.outside_charters.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={fieldLabel}>Outside the sprint's stories</div>
              <ul aria-label="Testing outside the sprint's stories" style={{ listStyle: 'none', margin: 0, padding: 0, maxWidth: 760 }}>
                {sprint.outside_charters.map((c) => (
                  <li key={c.id} style={{ fontSize: 12.5, padding: '6px 0', borderTop: '1px solid var(--border-soft)', display: 'flex', gap: 12, alignItems: 'baseline' }}>
                    <span style={{ flex: 1 }}>{c.mission_statement} <StoryChips stories={c.stories} /></span>
                    <span style={{ color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>{plural(c.sessions, 'session')}, {timeText(c.minutes)}</span>
                  </li>
                ))}
              </ul>
              <p style={{ ...quiet, marginTop: 6 }}>Testing outside the plan is often where the surprises are. It counts.</p>
            </div>
          )}
        </Section>

        <Section title="High risks not explored in these dates" count={sprint.high_unexplored.length}>
          {sprint.high_unexplored.length === 0 ? <p style={quiet}>Every high risk had at least one session in these dates.</p> : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, maxWidth: 760 }}>
              {sprint.high_unexplored.map((r) => (
                <li key={r.id} style={{ fontSize: 12.5, padding: '6px 0', borderTop: '1px solid var(--border-soft)', display: 'flex', gap: 12 }}>
                  <span className="mono" style={{ fontSize: 11, color: 'var(--danger)', minWidth: 52 }}>{r.ref} · {r.score}</span>
                  <span style={{ flex: 1 }}>{r.title}</span>
                  <span style={{ color: 'var(--text-tertiary)' }}>{r.in_sprint ? "in this sprint's stories" : 'in no story of this sprint'}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </main>
    </div>
  )
}
