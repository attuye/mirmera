/** Mirmera at work: the stages of a Jira work item it is planning (read → model → risks →
 *  charters → explore → done) or of an exploration started here, how far exploring has got,
 *  its last few steps with what it was thinking, and what it left behind.
 *  useAgentActivity polls quickly while something runs and slowly otherwise. */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bot, Check, CircleDashed, Loader2, X } from 'lucide-react'
import { AgentActivity, AgentActivityList, AgentStage } from '../api/client'
import { utcDate } from './Evidence'
import AgentBrowser from './AgentBrowser'
import TakeOver from './TakeOver'

const FAST_MS = 2000
const SLOW_MS = 15000

export function useAgentActivity(load: (() => Promise<AgentActivityList>) | null, key: string, idleMs = SLOW_MS): AgentActivityList | null {
  const [data, setData] = useState<AgentActivityList | null>(null)
  const timer = useRef<number | null>(null)
  useEffect(() => {
    if (!load) return
    let stopped = false
    const tick = async () => {
      let next: AgentActivityList | null = null
      try { next = await load() } catch { /* keep the last view; try again later */ }
      if (stopped) return
      if (next) setData(next)
      timer.current = window.setTimeout(tick, next && next.running > 0 ? FAST_MS : idleMs)
    }
    tick()
    return () => { stopped = true; if (timer.current) window.clearTimeout(timer.current) }
  }, [key])  // eslint-disable-line react-hooks/exhaustive-deps
  return data
}

const STAGE_COLOR: Record<AgentStage['status'], string> = {
  done: 'var(--accent)', active: 'var(--info)', todo: 'var(--text-quiet)', skipped: 'var(--text-quiet)',
  failed: 'var(--danger)', cancelled: 'var(--text-tertiary)',
}

function StageIcon({ status }: { status: AgentStage['status'] }) {
  const style = { flexShrink: 0, color: STAGE_COLOR[status] }
  if (status === 'done') return <Check size={13} aria-hidden style={style} />
  if (status === 'active') return <Loader2 size={13} aria-hidden className="mm-spin" style={style} />
  if (status === 'failed' || status === 'cancelled') return <X size={13} aria-hidden style={style} />
  return <CircleDashed size={13} aria-hidden style={style} />
}

const STEP_COLOR: Record<string, string> = {
  ok: 'var(--accent)', drafted: 'var(--danger)', failed: 'var(--warning)', refused: 'var(--warning)',
  'not verified': 'var(--warning)', stopped: 'var(--text-tertiary)',
}

function since(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - utcDate(iso).getTime()) / 1000))
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60 ? `${s % 60} s` : ''}`.trim()
}

export default function AgentProgress({ item, showTitle = true }: { item: AgentActivity; showTitle?: boolean }) {
  const ex = item.exploration
  const running = item.state === 'running'
  const base = item.project_id ? `/projects/${item.project_id}` : ''
  const pct = ex ? Math.min(100, Math.round((ex.steps_used / Math.max(ex.max_steps, 1)) * 100)) : 0
  const label = { running: 'Working', done: 'Done', failed: 'Stopped', cancelled: 'Canceled' }[item.state]
  return (
    <article aria-label={`Mirmera: ${item.issue_key ? item.issue_key + ' ' : ''}${item.title}`} style={{
      border: '1px solid var(--border)', borderLeft: `3px solid ${running ? 'var(--info)' : item.state === 'done' ? 'var(--accent)' : 'var(--danger)'}`,
      borderRadius: 8, padding: '12px 14px', background: 'var(--panel)',
    }}>
      {showTitle && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <Bot size={14} aria-hidden className={running ? 'mm-pulse' : undefined} style={{ color: running ? 'var(--info)' : 'var(--text-tertiary)', alignSelf: 'center' }} />
          {item.issue_key && <span className="mono" style={{ fontSize: 12 }}>{item.issue_key}</span>}
          <span style={{ fontSize: 13, fontWeight: 600, flex: 1, minWidth: 180 }}>{item.title}</span>
          <span role="status" style={{ fontSize: 11.5, color: running ? 'var(--info)' : 'var(--text-tertiary)' }}>
            {label}{running ? ` · ${since(item.started_at)}` : ''}{item.started_by ? ` · from ${item.started_by}` : ''}
          </span>
        </div>
      )}

      <ol aria-label="Stages" style={{ listStyle: 'none', display: 'flex', flexWrap: 'wrap', gap: '6px 16px', margin: showTitle ? '10px 0 0' : 0, padding: 0 }}>
        {item.stages.map((s) => (
          <li key={s.key} aria-current={s.status === 'active' ? 'step' : undefined} style={{
            display: 'inline-flex', gap: 5, alignItems: 'center', fontSize: 12,
            color: s.status === 'active' ? 'var(--text)' : s.status === 'todo' || s.status === 'skipped' ? 'var(--text-quiet)' : 'var(--text-secondary)',
            fontWeight: s.status === 'active' ? 600 : 400, textDecoration: s.status === 'skipped' ? 'line-through' : undefined,
          }} title={s.status === 'skipped' ? 'Skipped: the project has no app URL' : undefined}>
            <StageIcon status={s.status} />{s.label}
          </li>
        ))}
      </ol>

      {ex && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Exploring: {ex.mission}
          </div>
          <div role="progressbar" aria-label="Steps used" aria-valuemin={0} aria-valuemax={ex.max_steps} aria-valuenow={ex.steps_used}
            style={{ height: 6, borderRadius: 3, background: 'var(--border-soft)', marginTop: 6, overflow: 'hidden' }}>
            <div style={{ width: `${pct}%`, height: '100%', background: running ? 'var(--info)' : 'var(--accent)', transition: 'width .4s' }} />
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 4 }}>
            {ex.steps_used} of {ex.max_steps} steps · {ex.actions} action{ex.actions === 1 ? '' : 's'} in the app ·{' '}
            <span style={{ color: ex.bugs.length ? 'var(--danger)' : undefined }}>{ex.bugs.length} bug draft{ex.bugs.length === 1 ? '' : 's'}</span>
          </div>
          {running && ex.status === 'running' && ex.with_me && (
            <div style={{ marginTop: 8 }}>
              <TakeOver run={{
                id: ex.run_id, project_id: item.project_id ?? '', charter_id: ex.charter_id, session_id: ex.session_id,
                status: 'running', start_url: '', max_steps: ex.max_steps, steps: [], bug_ids: [], summary: null, detail: null,
                started_by: item.started_by, ai_provider: null, ai_model: null, created_at: item.started_at, finished_at: null,
                with_me: true, takeover: ex.takeover ?? null,
              }} />
            </div>
          )}
          {running && ex.status === 'running' && (
            <Link to={base && ex.session_id ? `${base}/sessions/${ex.session_id}` : '#'} title="Watch Mirmera in its session"
              style={{ display: 'block', marginTop: 10, maxWidth: 360 }}>
              <AgentBrowser compact run={{
                id: ex.run_id, project_id: item.project_id ?? '', charter_id: ex.charter_id, session_id: ex.session_id,
                status: 'running', start_url: '', max_steps: ex.max_steps, steps: [], bug_ids: [], summary: null, detail: null,
                started_by: item.started_by, ai_provider: null, ai_model: null, created_at: item.started_at, finished_at: null,
              }} />
            </Link>
          )}
          {ex.recent.length > 0 && (
            <ol aria-label="Latest steps" style={{ listStyle: 'none', margin: '8px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
              {ex.recent.map((s) => (
                <li key={s.n} style={{ fontSize: 12, display: 'flex', gap: 8, alignItems: 'baseline' }}>
                  <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-quiet)', minWidth: 22, textAlign: 'right' }}>{s.n}</span>
                  <span style={{ width: 7, height: 7, borderRadius: 4, flexShrink: 0, alignSelf: 'center', background: STEP_COLOR[s.status] ?? 'var(--text-quiet)' }} aria-hidden />
                  <span style={{ minWidth: 0 }}>
                    {s.by === 'Mirmera' && <span title={s.kind === 'tour' ? 'After an accepted submit, Mirmera opened the answer page directly, refreshed it, went back, sent the form twice quickly, made its request fail, and did it with the keyboard only' : s.kind === 'confirm' ? "When the run ended, Mirmera replayed each new draft's steps in fresh browsers" : s.kind === 'access' ? "Mirmera opened this user's own pages signed out and as the second user" : 'The AI was stuck, so Mirmera took this step itself'} style={{ fontSize: 10.5, marginRight: 6, padding: '1px 6px', borderRadius: 8, background: 'var(--accent-bg)', color: 'var(--accent)' }}>{s.kind === 'tour' ? "Mirmera's flow tour" : s.kind === 'confirm' ? "Mirmera's replay" : s.kind === 'access' ? "Mirmera: who sees what" : 'Mirmera stepped in'}</span>}
                    {s.by && s.by !== 'Mirmera' && <span title="The tester did this, in the same browser" style={{ fontSize: 10.5, marginRight: 6, padding: '1px 6px', borderRadius: 8, border: '1px solid var(--border)', color: 'var(--text)' }}>{s.by}</span>}
                    <span>{s.action}</span>
                    {s.status !== 'ok' && <span style={{ color: STEP_COLOR[s.status] ?? 'var(--text-tertiary)' }}> ({s.status})</span>}
                    {s.detail && <span style={{ color: 'var(--text-secondary)' }}> → {s.detail.length > 160 ? s.detail.slice(0, 157) + '…' : s.detail}</span>}
                    {s.thought && <span style={{ color: 'var(--text-tertiary)' }}> · {s.thought}</span>}
                  </span>
                </li>
              ))}
            </ol>
          )}
          {!running && ex.summary && <p style={{ fontSize: 12.5, margin: '8px 0 0', color: 'var(--text-secondary)' }}>{ex.summary}</p>}
        </div>
      )}

      {item.message && <p role="alert" style={{ fontSize: 12.5, margin: '8px 0 0', color: 'var(--danger)', whiteSpace: 'pre-line' }}>{item.message}</p>}

      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 10, fontSize: 12 }}>
        {base && item.story_id && <Link to={`${base}/stories/${item.story_id}`}>Open the story</Link>}
        {base && ex?.session_id && <Link to={`${base}/sessions/${ex.session_id}`}>Open the session</Link>}
        {base && ex && ex.bugs.length > 0 && <Link to={`${base}/bugs`}>Review the bug drafts</Link>}
      </div>
    </article>
  )
}
