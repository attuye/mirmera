import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, Note } from '../api/client'
import Sidebar from '../components/Sidebar'
import { errorText } from '../components/ui'
import BrowserPanel, { useRunner } from '../components/BrowserPanel'
import { Screenshot, clock } from '../components/Evidence'

const TAGS: { key: Note['tag']; label: string; color: string }[] = [
  { key: 'test', label: 'Test', color: 'var(--info)' },
  { key: 'bug', label: 'Bug', color: 'var(--danger)' },
  { key: 'issue', label: 'Issue', color: 'var(--warning)' },
  { key: 'question', label: 'Question', color: 'var(--question)' },
  { key: 'setup', label: 'Setup', color: 'var(--neutral-tag)' },
]

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
  const [ending, setEnding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [logError, setLogError] = useState<string | null>(null)
  const [paused, setPaused] = useState(false)
  const [pausedAt, setPausedAt] = useState<string | null>(null)
  const [totalPausedSeconds, setTotalPausedSeconds] = useState(0)
  const elapsed = useElapsed(startedAt, !ending, paused, pausedAt, totalPausedSeconds)
  const [charterMission, setCharterMission] = useState('')
  const runner = useRunner(sessionId)

  useEffect(() => {
    if (!sessionId) return
    api.listNotes(sessionId).then(setNotes).catch(console.error)
    api.getSession(sessionId).then((session) => {
      setStartedAt(session.started_at)
      setPaused(!!session.paused_at)
      setPausedAt(session.paused_at)
      setTotalPausedSeconds(session.total_paused_seconds)
      api.getCharter(session.charter_id).then((charter) => {
        setCharterMission(charter.mission_statement)
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
    await api.endSession(sessionId)
    navigate(`/sessions/${sessionId}/debrief`)
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
          </div>
          <div className="mono" style={{ fontSize: 26, fontWeight: 600, color: paused ? 'var(--text-tertiary)' : 'var(--accent)' }}>{elapsed}</div>
          <button onClick={togglePause} disabled={ending} style={{
            border: '1px solid #2E323A', background: 'transparent', color: '#C9CDD4', fontSize: 12.5, fontWeight: 500,
            padding: '10px 16px', borderRadius: 8, marginRight: 8,
          }}>
            {paused ? 'Resume' : 'Pause'}
          </button>
          <button onClick={endSession} disabled={ending} style={{
            background: ending ? 'var(--border)' : 'var(--danger)', color: '#0C0D0F', fontSize: 12.5, fontWeight: 600,
            padding: '10px 16px', borderRadius: 8, cursor: ending ? 'default' : 'pointer',
          }}>
            {ending ? 'Ending session… (generating debrief)' : 'End Session'}
          </button>
        </div>

        <div style={{ flex: 1, display: 'flex', padding: '22px 32px', gap: 24 }}>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <div style={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 11, padding: '14px 16px', marginBottom: 16 }}>
              <textarea
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
          {sessionId && <BrowserPanel sessionId={sessionId} runner={runner} disabled={ending} />}
        </div>
      </div>
    </div>
  )
}
