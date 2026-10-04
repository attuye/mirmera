/** Sprints: a name, dates, a goal and the stories planned for them. A sprint's page shows
 *  everything tested in its dates, under its stories or not, and what is still left. */
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import { ErrorLine, errorText, fieldLabel, input, primaryButton } from '../components/ui'
import { api, SprintSummary, StorySummary } from '../api/client'
import { useProject, useProjectPath } from '../project'
import { timeText } from './Stories'

const iso = (d: Date) => d.toISOString().slice(0, 10)
const addDays = (day: string, n: number) => { const d = new Date(day + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return iso(d) }
export const dayText = (day: string) => new Date(day + 'T00:00:00Z').toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' })
export const sprintDates = (s: Pick<SprintSummary, 'starts_on' | 'ends_on'>) => `${dayText(s.starts_on)} – ${dayText(s.ends_on)}`

export function SprintState({ sprint }: { sprint: Pick<SprintSummary, 'state' | 'days_left'> }) {
  const text = sprint.state === 'current' ? `Current · ${sprint.days_left} day${sprint.days_left === 1 ? '' : 's'} left` : sprint.state === 'past' ? 'Ended' : 'Upcoming'
  return (
    <span className="mono" style={{
      fontSize: 9.5, padding: '3px 7px', borderRadius: 5, whiteSpace: 'nowrap', textTransform: 'uppercase',
      background: sprint.state === 'current' ? 'var(--accent-bg)' : 'transparent', border: sprint.state === 'current' ? '1px solid transparent' : '1px solid var(--border)',
      color: sprint.state === 'current' ? 'var(--accent)' : 'var(--text-tertiary)',
    }}>{text}</span>
  )
}

function NewSprint({ last, stories, onAdded }: { last: SprintSummary | undefined; stories: StorySummary[]; onAdded: (id: string) => void }) {
  const { project } = useProject()
  // After the last sprint, as long as it was; otherwise two weeks from today.
  const start = last ? addDays(last.ends_on, 1) : iso(new Date())
  const length = last ? Math.round((Date.parse(last.ends_on) - Date.parse(last.starts_on)) / 86_400_000) : 13
  const number = last?.name.match(/(\d+)\s*$/)
  const [form, setForm] = useState({
    name: number ? last!.name.replace(/(\d+)\s*$/, String(Number(number[1]) + 1)) : last ? '' : 'Sprint 1',
    goal: '', starts_on: start, ends_on: addDays(start, length),
  })
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const open = stories.filter((s) => !s.closed)
  const valid = form.name.trim() && form.starts_on && form.ends_on && form.ends_on >= form.starts_on

  async function save() {
    setBusy(true); setError(null)
    try { onAdded((await api.createSprint(project.id, { ...form, story_ids: [...chosen] })).id) } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return (
    <section aria-label="New sprint" style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 16, maxWidth: 760, marginTop: 20 }}>
      <h2 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 10px' }}>New sprint</h2>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 150px 150px', gap: 10 }}>
        <div>
          <label htmlFor="sprint-name" style={fieldLabel}>Name</label>
          <input id="sprint-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={input} placeholder="Sprint 14" />
        </div>
        <div>
          <label htmlFor="sprint-start" style={fieldLabel}>Starts</label>
          <input id="sprint-start" type="date" value={form.starts_on} onChange={(e) => setForm({ ...form, starts_on: e.target.value })} style={input} />
        </div>
        <div>
          <label htmlFor="sprint-end" style={fieldLabel}>Ends (included)</label>
          <input id="sprint-end" type="date" value={form.ends_on} min={form.starts_on} onChange={(e) => setForm({ ...form, ends_on: e.target.value })} style={input} />
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <label htmlFor="sprint-goal" style={fieldLabel}>Goal (optional)</label>
          <input id="sprint-goal" value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} style={input}
            placeholder="e.g. Customers can verify with a passport end to end" />
        </div>
      </div>
      {open.length > 0 && (
        <fieldset style={{ border: 'none', padding: 0, margin: '12px 0 0' }}>
          <legend style={fieldLabel}>Stories in this sprint</legend>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 180, overflowY: 'auto' }}>
            {open.map((s) => (
              <label key={s.id} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5 }}>
                <input type="checkbox" checked={chosen.has(s.id)} onChange={() => setChosen((prev) => {
                  const next = new Set(prev); if (next.has(s.id)) next.delete(s.id); else next.add(s.id); return next
                })} />
                {s.key && <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{s.key}</span>}{s.title}
                {s.sprint && <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>(now in {s.sprint.name}: moves here)</span>}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
        <button onClick={save} disabled={!valid || busy} style={primaryButton(!valid || busy)}>{busy ? 'Adding…' : 'Add sprint'}</button>
      </div>
      <ErrorLine message={error} />
    </section>
  )
}

export default function Sprints() {
  const { project } = useProject()
  const path = useProjectPath()
  const navigate = useNavigate()
  const [sprints, setSprints] = useState<SprintSummary[] | null>(null)
  const [stories, setStories] = useState<StorySummary[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listSprints(project.id).then(setSprints).catch((e) => setError(errorText(e)))
    api.listStories(project.id).then(setStories).catch(() => {})
  }, [project.id])

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1100 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Sprints</h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 720, lineHeight: 1.5 }}>
          A sprint is its dates and the stories planned for it. Its page shows everything tested in those dates, including
          testing outside its stories, and high risks nobody explored. The Report and the Coverage map can show a sprint too.
        </p>
        <ErrorLine message={error} />
        {sprints && <NewSprint key={sprints[0]?.id ?? 'first'} last={sprints[0]} stories={stories} onAdded={(id) => navigate(path(`/sprints/${id}`))} />}

        <section aria-label="Your sprints" style={{ marginTop: 26, maxWidth: 900 }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 10px' }}>Your sprints</h2>
          {sprints && sprints.length === 0 && <p style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>No sprints yet.</p>}
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(sprints ?? []).map((s) => (
              <li key={s.id} aria-label={s.name} style={{
                border: `1px solid ${s.state === 'current' ? 'var(--accent)' : 'var(--border)'}`, borderRadius: 9, padding: '11px 14px',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <Link to={path(`/sprints/${s.id}`)} style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{s.name}</Link>
                  <span className="mono" style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{sprintDates(s)}</span>
                  <span style={{ marginLeft: 'auto' }}><SprintState sprint={s} /></span>
                </div>
                {s.goal && <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 4 }}>{s.goal}</div>}
                <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 6, fontSize: 12, color: 'var(--text-tertiary)' }}>
                  <span>{s.stories_done} of {s.stories} stor{s.stories === 1 ? 'y' : 'ies'} done</span>
                  <span>{s.sessions} session{s.sessions === 1 ? '' : 's'}{s.minutes ? `, ${timeText(s.minutes)}` : ''}</span>
                  <span>{s.bugs} bug{s.bugs === 1 ? '' : 's'}</span>
                  {s.state !== 'upcoming' && <span style={{ marginLeft: 'auto', color: s.left ? 'var(--warning)' : 'var(--text-tertiary)' }}>
                    {s.left ? `${s.left} thing${s.left === 1 ? '' : 's'} left in its stories` : 'Nothing obvious left in its stories'}
                  </span>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  )
}
