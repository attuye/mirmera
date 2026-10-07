import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Bot } from 'lucide-react'
import { api, ExplorationRun, Note } from '../api/client'
import Sidebar from '../components/Sidebar'
import { errorText, ConfirmDelete } from '../components/ui'
import BrowserPanel, { useRunner } from '../components/BrowserPanel'
import ApiPanel from '../components/ApiPanel'
import { Screenshot, clock } from '../components/Evidence'
import TestIdeas from '../components/TestIdeas'
import AgentBrowser from '../components/AgentBrowser'
import HeuristicChips from '../components/HeuristicChips'
import { useProject, useProjectPath, useSessionProject } from '../project'

const TAGS: { key: Note['tag']; label: string; color: string }[] = [
  { key: 'test', label: 'Test', color: 'var(--info)' },
  { key: 'bug', label: 'Bug', color: 'var(--danger)' },
  { key: 'issue', label: 'Issue', color: 'var(--warning)' },
  { key: 'question', label: 'Question', color: 'var(--question)' },
  { key: 'setup', label: 'Setup', color: 'var(--neutral-tag)' },
]

/** The exploration Mirmera's agent is running in this session, if any: checked on load, then
 *  every 2 s while it runs. onStep runs on each check (new notes), onDone once when it stops. */
function useAgentRun(charterId: string | null, sessionId: string | undefined, onDone: () => void, onStep: () => void): ExplorationRun | null {
  const [run, setRun] = useState<ExplorationRun | null>(null)
  useEffect(() => {
    if (!charterId || !sessionId) return
    let stopped = false
    let timer: number | undefined
    let wasRunning = false
    const tick = async () => {
      const latest = await api.charterExploration(charterId).catch(() => null)
      if (stopped) return
      const mine = latest && latest.session_id === sessionId ? latest : null
      setRun(mine)
      if (mine?.status === 'running') {
        if (wasRunning) onStep()
        wasRunning = true
        timer = window.setTimeout(tick, 2000)
      } else if (wasRunning) {
        onDone()
      }
    }
    tick()
    return () => { stopped = true; if (timer) window.clearTimeout(timer) }
  }, [charterId, sessionId])  // eslint-disable-line react-hooks/exhaustive-deps
  return run
}

/** Beside the clock while Mirmera's agent runs this session: how far it has got, and a way to stop it. */
function AgentAtWork({ run }: { run: ExplorationRun }) {
  const [stopping, setStopping] = useState(false)
  const last = run.steps[run.steps.length - 1]
  return (
    <div role="status" aria-label="Mirmera at work" title={last ? `Last step: ${last.action}` : undefined} style={{
      display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, padding: '6px 10px', borderRadius: 8,
      border: '1px solid var(--border)', background: 'var(--panel)', color: 'var(--info)', maxWidth: 320,
    }}>
      <Bot size={14} aria-hidden className="mm-pulse" style={{ flexShrink: 0 }} />
      <span style={{ minWidth: 0 }}>
        {stopping ? 'Mirmera is stopping…' : `Mirmera is exploring · step ${run.steps.length} of ${run.max_steps}`}
        {last && !stopping && <span style={{ display: 'block', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{last.action}</span>}
      </span>
      {!stopping && (
        <button onClick={() => { setStopping(true); api.cancelExploration(run.id).catch(() => setStopping(false)) }}
          style={{ fontSize: 11.5, padding: '3px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer' }}>
          Stop
        </button>
      )}
    </div>
  )
}

function useElapsed(startedAt: string | undefined, running: boolean, paused: boolean, pausedAt: string | null, totalPausedSeconds: number) {
  const [elapsed, setElapsed] = useState('00:00:00')
  useEffect(() => {
    if (!startedAt || !running) return
    const start = new Date(startedAt.endsWith('Z') ? startedAt : startedAt + 'Z').getTime()

    const compute = () => {
      // While paused, freeze the reference point at the moment it was paused
      // instead of "now" — that's what stops the clock from ticking on screen.
      const referenceNow = paused && pausedAt
        ? new Date(pausedAt.endsWith('Z') ? pausedAt : pausedAt + 'Z').getTime()
        : Date.now()
      const s = Math.max(0, Math.floor((referenceNow - start) / 1000) - Math.floor(totalPausedSeconds))
      const hh = String(Math.floor(s / 3600)).padStart(2, '0')
      const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
      const ss = String(s % 60).padStart(2, '0')
      setElapsed(`${hh}:${mm}:${ss}`)
    }

    compute()
    if (paused) return  // frozen — no ticking interval needed
    const id = setInterval(compute, 1000)
    return () => clearInterval(id)
  }, [startedAt, running, paused, pausedAt, totalPausedSeconds])
  return elapsed
}

export default function ActiveSession() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const [notes, setNotes] = useState<Note[]>([])
  const [draft, setDraft] = useState('')
  const [tag, setTag] = useState<Note['tag']>('test')
  const [startedAt, setStartedAt] = useState<string>()
  const navigate = useNavigate()
  const { project } = useProject()
  const path = useProjectPath()
  const [sessionProjectId, setSessionProjectId] = useState<string | null>(null)
  useSessionProject(sessionProjectId)
  const [ending, setEnding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [logError, setLogError] = useState<string | null>(null)
  const [tool, setTool] = useState<'browser' | 'api'>('browser')
  const [paused, setPaused] = useState(false)
  const [endError, setEndError] = useState<string | null>(null)
  const [pausedAt, setPausedAt] = useState<string | null>(null)
  const [totalPausedSeconds, setTotalPausedSeconds] = useState(0)
  const elapsed = useElapsed(startedAt, !ending, paused, pausedAt, totalPausedSeconds)
  const [charterMission, setCharterMission] = useState('')
  const [charterHeuristics, setCharterHeuristics] = useState<{ heuristics: string[]; oracles: string[] }>({ heuristics: [], oracles: [] })
  const runner = useRunner(sessionId)
  const box = useRef<HTMLTextAreaElement>(null)
  const [charterId, setCharterId] = useState<string | null>(null)
  const agentRun = useAgentRun(charterId, sessionId, () => {
    // Mirmera finished: show its last notes, and the pause it left the session in.
    if (!sessionId) return
    api.listNotes(sessionId).then(setNotes).catch(() => {})
    api.getSession(sessionId).then((s) => { setPaused(!!s.paused_at); setPausedAt(s.paused_at); setTotalPausedSeconds(s.total_paused_seconds) }).catch(() => {})
  }, () => { if (sessionId) api.listNotes(sessionId).then(setNotes).catch(() => {}) })
  const agentWorking = agentRun?.status === 'running'

  // Notes and bugs can also arrive from the Chrome extension's popup: refresh while it records.
  const extensionActive = !!runner.status?.extension_active
  useEffect(() => {
    if (!sessionId || !extensionActive) return
    const id = setInterval(() => { api.listNotes(sessionId).then(setNotes).catch(() => {}) }, 3000)
    return () => clearInterval(id)
  }, [sessionId, extensionActive])

  useEffect(() => {
    if (!sessionId) return
    api.listNotes(sessionId).then(setNotes).catch(console.error)
    api.getSession(sessionId).then((session) => {
      // Already ended (e.g. ended in another tab, or the page was refreshed while ending): show its debrief.
      if (session.ended_at) { navigate(path(`/sessions/${sessionId}/debrief`), { replace: true }); return }
      setStartedAt(session.started_at)
      setPaused(!!session.paused_at)
      setPausedAt(session.paused_at)
      setTotalPausedSeconds(session.total_paused_seconds)
      setSessionProjectId(session.project_id)
      setCharterId(session.charter_id)
      api.getCharter(session.charter_id).then((charter) => {
        setCharterMission(charter.mission_statement)
        setCharterHeuristics({ heuristics: charter.heuristics, oracles: charter.oracles })
      }).catch(console.error)
    }).catch(console.error)
  }, [sessionId])

  // Log the current text as a note, or as a bug (a bug also appears in the timeline).
  // Guarded so a note can't be lost: one save at a time, only the saved text is
  // cleared from the box, and a failed save keeps the text and says why.
  async function logEntry() {
    const text = draft.trim()
    if (!sessionId || !text || saving) return
    setSaving(true)
    setLogError(null)
    try {
      if (tag === 'bug') await api.logBug(sessionId, { title: text, severity: 'medium' })
      const note = await api.addNote(sessionId, tag, text)
      setNotes((prev) => [...prev, note])
      setDraft((current) => (current.trim() === text ? '' : current))
      // A bug logged with the browser running gets a screenshot; show it now.
      if (tag === 'bug' && runner.status?.running) runner.refresh().catch(console.error)
    } catch (e) {
      setLogError(`Not saved: ${errorText(e)} Your text is still in the box.`)
    } finally {
      setSaving(false)
    }
  }

  async function endSession() {
    if (!sessionId) return
    setEnding(true)
    setEndError(null)
    try {
      await api.endSession(sessionId)
    } catch (e) {
      // "Already ended" (a second click, another tab) or a dropped connection: if it did end, go on.
      const now = await api.getSession(sessionId).catch(() => null)
      if (!now?.ended_at) { setEnding(false); setEndError(`Not ended: ${errorText(e)}`); return }
    }
    navigate(path(`/sessions/${sessionId}/debrief`))
  }

  async function togglePause() {
    if (!sessionId) return
    if (paused) {
      const session = await api.resumeSession(sessionId)
      setPaused(false)
      setPausedAt(null)
      setTotalPausedSeconds(session.total_paused_seconds)
    } else {
      const session = await api.pauseSession(sessionId)
      setPaused(true)
      setPausedAt(session.paused_at)
    }
  }

  // Notes and screenshots in one timeline. Both timestamps are naive UTC ISO
  // strings, so they sort correctly as text.
  const timeline = [
    ...notes.map((note) => ({ type: 'note' as const, at: note.timestamp, note })),
    ...runner.evidence.filter((e) => e.kind === 'screenshot').map((shot) => ({ type: 'shot' as const, at: shot.captured_at, shot })),
  ].sort((a, b) => a.at.localeCompare(b.at))

  return (
    <div style={{ minHeight: '100vh', display: 'flex' }}>
      <Sidebar />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '18px 36px', borderBottom: '1px solid var(--border-soft)',
        }}>
          <div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--text-quiet)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Charter</div>
            <div style={{ fontSize: 14.5, fontWeight: 600, maxWidth: 560 }}>{charterMission || 'Loading…'}</div>
            <HeuristicChips heuristics={charterHeuristics.heuristics} oracles={charterHeuristics.oracles} />
            {sessionId && (
              <div style={{ marginTop: 6 }}>
                <ConfirmDelete label="Delete session" question="Delete this session with its notes, bugs and recordings?"
                  onConfirm={async () => { await api.deleteSession(sessionId); navigate(path('/dashboard'), { replace: true }) }} />
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {agentRun && agentWorking && <AgentAtWork run={agentRun} />}
            <div className="mono" style={{ fontSize: 26, fontWeight: 600, color: paused ? 'var(--text-tertiary)' : 'var(--accent)' }}>{elapsed}</div>
          </div>
          <button onClick={togglePause} disabled={ending || agentWorking} style={{
            border: '1px solid #2E323A', background: 'transparent', color: '#C9CDD4', fontSize: 12.5, fontWeight: 500,
            padding: '10px 16px', borderRadius: 8, marginRight: 8,
          }}>
            {paused ? 'Resume' : 'Pause'}
          </button>
          <button onClick={endSession} disabled={ending || agentWorking} title={agentWorking ? 'Mirmera is still exploring: stop it first' : undefined} style={{
            background: ending || agentWorking ? 'var(--border)' : 'var(--danger)', color: '#0C0D0F', fontSize: 12.5, fontWeight: 600,
            padding: '10px 16px', borderRadius: 8, cursor: ending || agentWorking ? 'default' : 'pointer',
          }}>
            {ending ? 'Ending session…' : 'End Session'}
          </button>
        </div>
        {endError && <div role="alert" style={{ padding: '8px 36px', color: 'var(--danger)', fontSize: 12.5 }}>{endError}</div>}

        <div style={{ flex: 1, display: 'flex', padding: '22px 32px', gap: 24 }}>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            {agentRun && <div style={{ marginBottom: 16 }}><AgentBrowser run={agentRun} /></div>}
            <div style={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 11, padding: '14px 16px', marginBottom: 16 }}>
              <textarea
                ref={box}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); logEntry() } }}
                placeholder="What are you observing?"
                aria-label="What are you observing?"
                style={{
                  width: '100%', minHeight: 50, background: 'transparent', border: 'none', color: 'var(--text)',
                  fontSize: 13, resize: 'vertical', outline: 'none', fontFamily: 'var(--font-ui)',
                }}
              />
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', gap: 6 }}>
                  {TAGS.map((t) => (
                    <button key={t.key} onClick={() => setTag(t.key)} style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      background: tag === t.key ? `${t.color}22` : 'transparent',
                      color: tag === t.key ? t.color : 'var(--text-secondary)',
                      fontSize: 11.5, fontWeight: 500, padding: '6px 11px', borderRadius: 7,
                    }}>
                      <div style={{ width: 6, height: 6, borderRadius: '50%', background: t.color }} />
                      {t.label}
                    </button>
                  ))}
                </div>
                <button onClick={logEntry} disabled={saving || !draft.trim()} title="Ctrl+Enter" style={{
                  background: saving || !draft.trim() ? 'var(--border)' : 'var(--accent)', color: '#0C0D0F',
                  fontSize: 12, fontWeight: 600, padding: '7px 16px', borderRadius: 7,
                }}>
                  {saving ? 'Saving…' : tag === 'bug' ? 'Log Bug' : 'Log Note'}
                </button>
              </div>
            </div>

            {logError && (
              <div role="alert" style={{ color: 'var(--danger)', fontSize: 12.5, margin: '-6px 0 12px' }}>{logError}</div>
            )}
            {sessionId && (
              <TestIdeas sessionId={sessionId} disabled={ending || paused} refreshKey={notes.length}
                onNote={(note) => setNotes((prev) => [...prev, note])}
                onUse={(text) => { setTag('test'); setDraft(text); box.current?.focus() }} />
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 3, overflowY: 'auto' }}>
              {timeline.map((item) => item.type === 'note' ? (
                <div key={item.note.id} style={{ display: 'flex', gap: 12, padding: '10px 4px', borderBottom: '1px solid #17191D' }}>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)', width: 80, flexShrink: 0, whiteSpace: 'nowrap' }}>
                    {clock(item.note.timestamp)}
                  </div>
                  <div style={{ width: 7, height: 7, borderRadius: '50%', background: TAGS.find((t) => t.key === item.note.tag)!.color, marginTop: 6, flexShrink: 0 }} />
                  <div style={{ fontSize: 13, lineHeight: 1.5 }}>{item.note.content}</div>
                </div>
              ) : (
                <div key={item.shot.id} style={{ display: 'flex', gap: 12, padding: '10px 4px', borderBottom: '1px solid #17191D' }}>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)', width: 80, flexShrink: 0, whiteSpace: 'nowrap' }}>
                    {clock(item.shot.captured_at)}
                  </div>
                  <div style={{ width: 7, height: 7, borderRadius: 2, background: 'var(--accent)', marginTop: 6, flexShrink: 0 }} />
                  <Screenshot e={item.shot} width={200} />
                </div>
              ))}
            </div>
          </div>
          {sessionId && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, flexShrink: 0 }}>
              <div role="tablist" aria-label="Tools" style={{ display: 'inline-flex', alignSelf: 'flex-start', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
                {(['browser', 'api'] as const).map((t) => (
                  <button key={t} role="tab" aria-selected={tool === t} onClick={() => setTool(t)} style={{
                    border: 'none', padding: '6px 14px', fontSize: 12.5, cursor: 'pointer', fontFamily: 'var(--font-ui)',
                    background: tool === t ? 'var(--accent-bg)' : 'transparent', color: tool === t ? 'var(--accent)' : 'var(--text-secondary)',
                  }}>{t === 'browser' ? 'Browser' : 'API'}</button>
                ))}
              </div>
              <div hidden={tool !== 'browser'}><BrowserPanel sessionId={sessionId} runner={runner} disabled={ending} project={project} /></div>
              {tool === 'api' && <ApiPanel sessionId={sessionId} project={project} disabled={ending}
                onNote={(note) => setNotes((prev) => [...prev, note])} />}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
