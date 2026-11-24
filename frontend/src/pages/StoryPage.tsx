/** One user story: what it claims, the risks and charters linked to it, the sessions and
 *  bugs under those charters, the pages explored, and what is still left to look at. */
import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ExternalLink, RefreshCw } from 'lucide-react'
import Sidebar from '../components/Sidebar'
import ExploreButton from '../components/ExploreButton'
import AgentProgress, { useAgentActivity } from '../components/AgentProgress'
import RiskForm from '../components/RiskForm'
import SuggestedRisks from '../components/SuggestedRisks'
import SuggestionSkeleton from '../components/SuggestionSkeleton'
import CharterDrafts from '../components/CharterDrafts'
import MissionEditor from '../components/MissionEditor'
import StoryEditor from '../components/StoryEditor'
import { useAiJob } from '../components/useAiJob'
import { utcDate } from '../components/Evidence'
import { ConfirmDelete, ErrorLine, errorText, input, quietButton } from '../components/ui'
import { api, Charter, Risk, SprintSummary, StoryChartersResult, StoryDetail, StoryRisksResult } from '../api/client'
import { useProject, useProjectPath } from '../project'
import { StoryStatus, timeText } from './Stories'

const SEVERITY_COLOR = { high: 'var(--danger)', medium: 'var(--warning)', low: 'var(--text-tertiary)' }
const CLAIM_LINES = 8
const plural = (n: number, word: string) => (n === 1 ? word : `${word}s`)

function Section({ id, title, count, children, aside }: { id?: string; title: string; count?: number; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section id={id} aria-label={title} style={{ marginTop: 26, scrollMarginTop: 20 }}>
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

const row: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 9, padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'flex-start' }
const small: React.CSSProperties = { ...quietButton, fontSize: 12, padding: '4px 10px' }
const quiet: React.CSSProperties = { fontSize: 12.5, color: 'var(--text-tertiary)', margin: 0 }

function Claims({ text }: { text: string | null }) {
  const [all, setAll] = useState(false)
  if (!text) return <p style={quiet}>No description. Ask what the story is meant to do, for whom, and how you'd know it doesn't.</p>
  const lines = text.split('\n')
  const long = lines.length > CLAIM_LINES
  return (
    <div>
      <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap', color: 'var(--text-secondary)', maxWidth: 760 }}>
        {all || !long ? text : lines.slice(0, CLAIM_LINES).join('\n') + '…'}
      </div>
      {long && <button onClick={() => setAll(!all)} style={{ ...small, marginTop: 6 }}>{all ? 'Show less' : `Show all ${lines.length} lines`}</button>}
    </div>
  )
}

function Picker<T extends { id: string }>({ label, options, text, onPick }: { label: string; options: T[]; text: (o: T) => string; onPick: (id: string) => Promise<void> }) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  if (!options.length) return null
  return (
    <span style={{ display: 'inline-flex', gap: 6 }}>
      <select aria-label={label} value={value} onChange={(e) => setValue(e.target.value)} style={{ ...input, width: 300, fontSize: 12, padding: '4px 6px' }}>
        <option value="">{label}…</option>
        {options.map((o) => <option key={o.id} value={o.id}>{text(o)}</option>)}
      </select>
      <button disabled={!value || busy} style={small} onClick={async () => {
        setBusy(true)
        try { await onPick(value); setValue('') } finally { setBusy(false) }
      }}>Link</button>
    </span>
  )
}

export default function StoryPage() {
  const { storyId = '' } = useParams()
  const { project } = useProject()
  const path = useProjectPath()
  const navigate = useNavigate()
  const [story, setStory] = useState<StoryDetail | null>(null)
  const [risks, setRisks] = useState<Risk[]>([])
  const [charters, setCharters] = useState<Charter[]>([])
  const [sprints, setSprints] = useState<SprintSummary[]>([])
  const { hash, state } = useLocation()
  const [sprintNote, setSprintNote] = useState<string | null>((state as { sprintNote?: string } | null)?.sprintNote ?? null)
  const [addingRisk, setAddingRisk] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [watch, setWatch] = useState(0)  // bumped when an exploration starts here: look again at once
  const activity = useAgentActivity(() => api.storyActivity(storyId), `${storyId}-${watch}`)
  const wasRunning = useRef(0)
  const charterJob = useAiJob<StoryChartersResult>(project.id, 'story_charters', undefined, storyId)
  const suggest = useAiJob<StoryRisksResult>(project.id, 'story_risks', () => {  // new proposed risks are on the story now
    api.getStory(storyId).then(setStory).catch(() => {})
    api.listRisks(project.id).then(setRisks).catch(() => {})
  }, storyId)

  useEffect(() => {  // Mirmera just finished something here: show what it made
    if (!activity) return
    if (wasRunning.current > activity.running) api.getStory(storyId).then(setStory).catch(() => {})
    wasRunning.current = activity.running
  }, [activity, storyId])

  useEffect(() => {
    api.getStory(storyId).then(setStory).catch((e) => setError(errorText(e)))
    api.listRisks(project.id).then(setRisks).catch(() => {})
    api.listCharters(project.id).then(setCharters).catch(() => {})
    api.listSprints(project.id).then(setSprints).catch(() => {})
  }, [storyId, project.id])
  useEffect(() => {  // a link to /stories/x#charters (from a sprint's page) scrolls there
    if (story && hash) document.getElementById(hash.slice(1))?.scrollIntoView({ block: 'start' })
  }, [story?.id, hash])  // eslint-disable-line react-hooks/exhaustive-deps

  async function act(what: string, fn: () => Promise<StoryDetail>) {
    setBusy(what); setError(null)
    try {
      const s = await fn()
      setStory(s)
      if (what === 'refresh') {
        setSprintNote(s.sprint_note ?? null)
        if (s.sprint_note) api.listSprints(project.id).then(setSprints).catch(() => {})  // it may have made one
      }
    } catch (e) { setError(errorText(e)) } finally { setBusy(null) }
  }
  if (!story) return <div style={{ display: 'flex', minHeight: '100vh' }}><Sidebar /><main style={{ padding: '32px 40px' }}>{error ? <ErrorLine message={error} /> : 'Loading…'}</main></div>

  const riskIds = story.risk_list.map((r) => r.id)
  const suggested = suggest.job?.status === 'done' && suggest.job.result?.story_id === story.id ? suggest.job.result : null
  async function decide(riskId: string, action: 'link' | 'drop') {
    await act('suggest', () => api.decideRiskSuggestion(story!.id, riskId, action))
    if (suggest.job?.result) suggest.setJob({ ...suggest.job, result: { ...suggest.job.result,
      links: suggest.job.result.links.map((l) => l.risk_id === riskId ? { ...l, status: action === 'link' ? 'linked' : 'dropped' } : l) } })
  }
  const drafts = charterJob.job?.status === 'done' && charterJob.job.result?.story_id === story.id ? charterJob.job.result : null
  async function acceptDraft(index: number, mission: string) {  // a charter planned from the story's risks
    const d = drafts!.drafts[index], job = charterJob.job!
    await act('charter', async () => {
      const charter = await api.saveCharter(project.id, {
        mission_statement: mission, rationale: d.rationale, priority: d.priority, source: 'risk-based',
        risk_ids: d.risk_ids, test_ideas: d.test_ideas, oracles: d.oracles, story_ids: [story!.id],
        risk_areas: story!.risk_list.filter((r) => d.risk_ids.includes(r.id)).map((r) => r.title),
      })
      charterJob.setJob(await api.markJobItem(job.id, index, 'accepted', charter.id) as typeof job)
      return api.getStory(story!.id)
    })
  }
  async function dropDraft(index: number) {
    const job = charterJob.job!
    setBusy('charter'); setError(null)
    try { charterJob.setJob(await api.markJobItem(job.id, index, 'dropped') as typeof job) } catch (e) { setError(errorText(e)) } finally { setBusy(null) }
  }
  async function review(riskId: string, accept: boolean) {  // a proposed risk on this story: accept it, or drop it from the project
    await act('review', async () => {
      await api.updateRisk(riskId, { status: accept ? 'accepted' : 'retired' })
      return accept ? api.getStory(story!.id) : api.setStoryRisks(story!.id, riskIds.filter((x) => x !== riskId))
    })
    api.listRisks(project.id).then(setRisks).catch(() => {})
  }
  const charterIds = story.charter_list.map((c) => c.id)
  const go = (to: string) => to.startsWith('#') ? document.getElementById(to.slice(1))?.scrollIntoView({ behavior: 'smooth' }) : navigate(path(to))
  const trackerName = story.tracker === 'jira' ? 'Jira' : story.tracker === 'github' ? 'GitHub' : null

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '28px 40px 60px', maxWidth: 1000 }}>
        <Link to={path('/stories')} style={{ fontSize: 12.5, color: 'var(--text-tertiary)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <ArrowLeft size={13} aria-hidden /> Stories
        </Link>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginTop: 10 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {story.key && (story.url
                ? <a href={story.url} target="_blank" rel="noreferrer" className="mono" style={{ fontSize: 12, color: 'var(--info)', display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                    {story.key} <ExternalLink size={11} aria-hidden /></a>
                : <span className="mono" style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{story.key}</span>)}
              <StoryStatus story={story} />
            </div>
            <h1 style={{ fontSize: 21, fontWeight: 600, margin: '6px 0 0', lineHeight: 1.35 }}>{story.title}</h1>
          </div>
          <div style={{ display: 'flex', gap: 8, flexShrink: 0, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            {!editing && <button disabled={!!busy} onClick={() => setEditing(true)} style={quietButton}>Edit story</button>}
            {trackerName
              ? <button disabled={!!busy} onClick={() => act('refresh', () => api.refreshStory(story.id))} style={{ ...quietButton, display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                  <RefreshCw size={13} aria-hidden /> {busy === 'refresh' ? 'Reading…' : `Refresh from ${trackerName}`}</button>
              : <button disabled={!!busy} onClick={() => act('close', () => api.updateStory(story.id, { closed: !story.closed }))} style={quietButton}>
                  {story.closed ? 'Reopen' : 'Mark done'}</button>}
            <ConfirmDelete label="Delete story" question="Delete this story? Its risks, charters and sessions stay in the project."
              onConfirm={async () => { await api.deleteStory(story.id); navigate(path('/stories')) }} />
          </div>
        </div>
        <div role="group" aria-label="Totals" style={{ display: 'flex', gap: 22, marginTop: 14, fontSize: 12.5, color: 'var(--text-secondary)', flexWrap: 'wrap' }}>
          <span><b>{story.risks}</b> {plural(story.risks, 'risk')}</span><span><b>{story.charters}</b> {plural(story.charters, 'charter')}</span>
          <span><b>{story.sessions}</b> {plural(story.sessions, 'session')}{story.minutes ? `, ${timeText(story.minutes)}` : ''}</span>
          <span><b>{story.bugs}</b> {plural(story.bugs, 'bug')}{story.high_bugs ? <span style={{ color: 'var(--danger)' }}> ({story.high_bugs} high)</span> : ''}</span>
          {sprints.length > 0 && (
            <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center', marginLeft: 'auto' }}>Sprint
              <select aria-label="Sprint" value={story.sprint?.id ?? ''} disabled={!!busy}
                onChange={(e) => act('sprint', () => api.updateStory(story.id, { sprint_id: e.target.value || null }))}
                style={{ ...input, width: 'auto', fontSize: 12, padding: '2px 6px' }}>
                <option value="">None</option>
                {sprints.map((s) => <option key={s.id} value={s.id}>{s.name}{s.state === 'current' ? ' (current)' : ''}</option>)}
              </select>
            </label>
          )}
        </div>
        <ErrorLine message={error} />
        {editing && (
          <StoryEditor story={story} trackerName={trackerName}
            onSaved={(s) => { setStory(s); setEditing(false) }} onCancel={() => setEditing(false)} />
        )}
        {sprintNote && (
          <p role="status" aria-label="Sprint from Jira" style={{ margin: '12px 0 0', fontSize: 12.5, color: 'var(--text-secondary)', padding: '7px 12px', borderLeft: '3px solid var(--info)', background: 'var(--panel)', borderRadius: 6, maxWidth: 820 }}>
            {sprintNote}</p>
        )}
        {activity && activity.items.length > 0 && (
          <section aria-label="Mirmera at work" style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 820 }}>
            {activity.items.map((i) => <AgentProgress key={i.id} item={i} />)}
          </section>
        )}

        <Section title="What it claims">
          <Claims text={story.description} />
        </Section>

        <Section title="What's left" count={story.left_list.length}>
          {story.left_list.length === 0
            ? <p style={quiet}>Nothing obvious left. That isn't a verdict: decide whether the story has been explored enough for the people who matter.</p>
            : <ul aria-label="What's left" style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6, maxWidth: 820 }}>
                {story.left_list.map((l, i) => (
                  <li key={i} style={{ display: 'flex', gap: 12, alignItems: 'center', fontSize: 13, padding: '7px 12px', borderLeft: '3px solid var(--warning)', background: 'var(--panel)', borderRadius: 6 }}>
                    <span style={{ flex: 1 }}>{l.text}</span>
                    <button onClick={() => go(l.to)} style={small}>{l.action}</button>
                  </li>
                ))}
              </ul>}
        </Section>

        <Section id="risks" title="Risks" count={story.risk_list.length} aside={
          <span style={{ display: 'inline-flex', gap: 8 }}>
            <Picker label="Link a project risk" options={risks.filter((r) => !riskIds.includes(r.id))} text={(r) => `${r.ref} · ${r.score} · ${r.title}`}
              onPick={(id) => act('risk', () => api.setStoryRisks(story.id, [...riskIds, id]))} />
            <button onClick={() => setAddingRisk(!addingRisk)} style={small}>{addingRisk ? 'Cancel' : 'New risk'}</button>
            <button disabled={suggest.running || !!busy} onClick={() => suggest.start({ story_id: story.id })} style={small}
              title="The project's risks this story touches, and new ones it brings in. Everything is a proposal.">
              {suggest.running ? `Thinking… ${suggest.since}` : 'Suggest risks'}</button>
          </span>}>
          <ErrorLine message={suggest.error} />
          {suggested && <SuggestedRisks result={suggested} busy={!!busy} onDecide={decide} />}
          {addingRisk && <RiskForm label="Add risk to this story" save={async (r) => {
            const updated = await api.newStoryRisk(story.id, r)
            setStory(updated); setAddingRisk(false)
            api.listRisks(project.id).then(setRisks).catch(() => {})
          }} />}
          {story.risk_list.length === 0 && !addingRisk && <p style={quiet}>What could this change break, and for whom? Link a project risk, add a new one, or let Mirmera suggest some.</p>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {story.risk_list.map((r) => (
              <div key={r.id} role="article" aria-label={`${r.ref} ${r.title}`} style={{ ...row, opacity: r.status === 'retired' ? 0.6 : 1 }}>
                <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', minWidth: 52 }}>{r.ref} · {r.score}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{r.title}{r.status !== 'accepted' && <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}> ({r.status})</span>}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>{r.failure_mode}</div>
                  <div style={{ fontSize: 11.5, marginTop: 4, color: r.charters ? 'var(--text-tertiary)' : 'var(--warning)' }}>
                    {r.charters ? `${r.charters} charter${r.charters === 1 ? '' : 's'} here investigate${r.charters === 1 ? 's' : ''} it` : 'No charter here investigates it'}
                    {r.other_stories.length > 0 && <> · also in {r.other_stories.map((s, i) => (
                      <span key={s.id}>{i > 0 && ', '}<Link to={path(`/stories/${s.id}`)}>{s.key ?? s.title}</Link></span>))}</>}
                  </div>
                </div>
                {r.status === 'proposed' && <>
                  <button disabled={!!busy} onClick={() => review(r.id, true)} style={small} aria-label={`Accept ${r.ref}`}>Accept</button>
                  <button disabled={!!busy} onClick={() => review(r.id, false)} style={small} aria-label={`Drop ${r.ref}`}
                    title="Retire the risk and take it off this story">Drop</button>
                </>}
                <button disabled={!!busy} onClick={() => act('risk', () => api.setStoryRisks(story.id, riskIds.filter((x) => x !== r.id)))} style={small}
                  aria-label={`Unlink ${r.ref}`}>Unlink</button>
              </div>
            ))}
          </div>
        </Section>

        <Section id="charters" title="Charters" count={story.charter_list.length} aside={
          <span style={{ display: 'inline-flex', gap: 8 }}>
            <Picker label="Link a charter" options={charters.filter((c) => !charterIds.includes(c.id))} text={(c) => c.mission_statement.slice(0, 80)}
              onPick={(id) => act('charter', () => api.setStoryCharters(story.id, [...charterIds, id]))} />
            <button disabled={charterJob.running || !!busy} onClick={() => charterJob.start({ story_id: story.id })} style={small}
              title="Charters planned from the story's accepted risks, in the template Explore … with … to discover ….">
              {charterJob.running ? `Thinking… ${charterJob.since}` : 'Suggest charters'}</button>
          </span>}>
          <ErrorLine message={charterJob.error} />
          {charterJob.running && <SuggestionSkeleton count={2}
            label={`Planning charters from the story's accepted risks… ${charterJob.since}. A local model can take a few minutes: you can leave this page, the suggestions will be here when you come back.`} />}
          {drafts && (
            <div style={{ marginBottom: 12 }}>
              {drafts.drafts.some((d) => d.status === 'suggested') && (
                <p style={{ ...quiet, marginBottom: 8 }}>Suggested from the accepted risks: edit the mission if it needs it, then accept or drop each one.
                  {drafts.uncovered_risk_refs.length > 0 && ` Not covered: ${drafts.uncovered_risk_refs.join(', ')}.`}</p>
              )}
              <CharterDrafts jobId={charterJob.job!.id} busy={!!busy}
                items={drafts.drafts.map((d) => ({ mission: d.mission_statement, rationale: d.rationale, priority: d.priority, status: d.status,
                  investigates: d.risk_refs, ideas: d.test_ideas }))}
                onAccept={acceptDraft} onDrop={dropDraft} />
              {drafts.drafts.length === 0 && <p style={quiet}>No new charters: the story's charters already cover its accepted risks.</p>}
            </div>
          )}
          {story.charter_list.length === 0 && !drafts && <p style={quiet}>No charters yet. Accept the story's risks, then suggest charters from them, or link one the project already has.</p>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {story.charter_list.map((c) => (
              <div key={c.id} role="article" aria-label={c.mission_statement} style={row}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <MissionEditor value={c.mission_statement} style={{ fontSize: 13 }} canEdit={c.sessions === 0}
                    why="A session has used this charter: its mission stays as they explored it."
                    onSave={async (m) => { await api.updateCharter(c.id, { mission_statement: m }); setStory(await api.getStory(story.id)) }} />
                  <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 4, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    <span className="mono" style={{ fontSize: 9.5, color: c.status === 'active' ? 'var(--accent)' : 'var(--text-tertiary)' }}>{c.status.toUpperCase()}</span>
                    {c.source === 'follow-up' && <span title={c.rationale ?? undefined} style={{ color: 'var(--info)' }}>follow-up from Mirmera</span>}
                    {c.risk_refs.length > 0 && <span>investigates {c.risk_refs.join(', ')}</span>}
                    <span>{c.sessions ? `${c.sessions} session${c.sessions === 1 ? '' : 's'}, ${timeText(c.minutes)}` : 'not explored yet'}</span>
                    {c.bugs > 0 && <span>{c.bugs} bug{c.bugs === 1 ? '' : 's'}</span>}
                  </div>
                  <ExploreButton charterId={c.id} onStarted={() => setWatch((w) => w + 1)} onFinished={() => api.getStory(story.id).then(setStory).catch(() => {})} />
                </div>
                <button disabled={!!busy} onClick={() => act('charter', () => api.setStoryCharters(story.id, charterIds.filter((x) => x !== c.id)))} style={small}
                  aria-label={`Unlink ${c.mission_statement}`}>Unlink</button>
              </div>
            ))}
          </div>
        </Section>

        <Section title="Sessions" count={story.session_list.length}>
          {story.session_list.length === 0 ? <p style={quiet}>None yet. Start one from a charter on the Charters page.</p> : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {story.session_list.map((s) => (
                <li key={s.id} aria-label={`Session by ${s.tester}`} title={s.ended_at ? 'Open the debrief' : 'Open the session'}
                  onClick={(e) => { if (!(e.target as HTMLElement).closest('a')) navigate(path(s.ended_at ? `/sessions/${s.id}/debrief` : `/sessions/${s.id}`)) }}
                  style={{ display: 'flex', gap: 12, alignItems: 'baseline', fontSize: 12.5, padding: '7px 0', borderTop: '1px solid var(--border-soft)', cursor: 'pointer' }}>
                  <span className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)', minWidth: 90 }}>{utcDate(s.started_at).toLocaleDateString()}</span>
                  <span style={{ minWidth: 70 }}>{s.tester}</span>
                  <span style={{ flex: 1, color: 'var(--text-secondary)' }}>{s.mission_statement}</span>
                  <span style={{ color: 'var(--text-tertiary)' }}>{timeText(s.minutes)}{s.bugs ? `, ${s.bugs} bug${s.bugs === 1 ? '' : 's'}` : ''}</span>
                  {s.ended_at
                    ? <span style={{ display: 'inline-flex', gap: 10 }}><Link to={path(`/sessions/${s.id}?view=1`)}>Session</Link><Link to={path(`/sessions/${s.id}/debrief`)}>{s.debrief ? 'Debrief' : 'Write debrief'}</Link></span>
                    : <Link to={path(`/sessions/${s.id}`)}>{s.paused ? 'Paused' : 'Running'}</Link>}
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Bugs" count={story.bug_list.length}>
          {story.bug_list.length === 0 ? <p style={quiet}>None logged under this story's charters.</p> : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {story.bug_list.map((b) => (
                <li key={b.id} style={{ display: 'flex', gap: 12, alignItems: 'baseline', fontSize: 12.5, padding: '7px 0', borderTop: '1px solid var(--border-soft)' }}>
                  <span className="mono" style={{ fontSize: 9.5, minWidth: 64, color: b.kind === 'bug' ? SEVERITY_COLOR[b.severity] : 'var(--text-tertiary)' }}>
                    {b.kind === 'bug' ? b.severity.toUpperCase() : b.kind === 'question' ? 'QUESTION' : 'TO REVIEW'}
                  </span>
                  <span style={{ flex: 1 }}>{b.title}</span>
                  {b.issue_key && b.issue_url
                    ? <a href={b.issue_url} target="_blank" rel="noreferrer">{b.issue_key} ↗</a>
                    : <Link to={path('/bugs')}>{b.kind ? 'Not filed' : 'Review'}</Link>}
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Pages explored" count={story.pages.length + story.more_pages}>
          {story.pages.length === 0 ? <p style={quiet}>Nothing recorded yet. Pages appear here once a session uses the recorded browser or the extension.</p> : (
            <>
              <ul style={{ listStyle: 'none', margin: 0, padding: 0, maxWidth: 640 }}>
                {story.pages.map((p) => (
                  <li key={p.page} style={{ display: 'flex', gap: 12, fontSize: 12.5, padding: '5px 0', borderTop: '1px solid var(--border-soft)' }}>
                    <span className="mono" style={{ flex: 1, fontSize: 12 }}>{p.page}</span>
                    <span style={{ color: 'var(--text-tertiary)' }}>{p.sessions} session{p.sessions === 1 ? '' : 's'}, {p.actions} action{p.actions === 1 ? '' : 's'}</span>
                  </li>
                ))}
              </ul>
              <p style={{ ...quiet, marginTop: 8 }}>
                {story.more_pages > 0 && `And ${story.more_pages} more. `}A change often breaks something next to it: check the{' '}
                <Link to={path('/coverage')}>coverage map</Link> for nearby pages nobody tried.
              </p>
            </>
          )}
        </Section>
      </main>
    </div>
  )
}
