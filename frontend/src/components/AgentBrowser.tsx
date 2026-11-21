/** Mirmera's browser: watch the exploration agent use the app.
 *  - Live while it works (a fresh picture every 400 ms).
 *  - Afterwards, its recording plays like a video: play, pause, speed, jump step to step;
 *    the caption follows the step on screen, with a box on the element it was about to use.
 *  - Runs without a recording (older ones), and looking back while it works, play the
 *    step pictures one after another instead. */
import { useEffect, useRef, useState } from 'react'
import { Bot, ChevronLeft, ChevronRight, Download, Pause, Play } from 'lucide-react'
import { api, ExplorationRun, explorationLiveUrl, explorationHarUrl, explorationStepFrameUrl, explorationVideoUrl } from '../api/client'
import { errorText } from './ui'

type Step = ExplorationRun['steps'][number]
const BOX_SECONDS = 1.5  // in the recording, the box shows this long after the step starts
const STILL_MS = 1500  // playing the pictures: each one this long at 1×
const SPEEDS = [1, 2, 4]
const clockText = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`

const DESKTOP = { w: 1280, h: 800 }, PHONE = { w: 390, h: 844 }  // the agent's viewports (backend/app/agent/explore.py)

/** The live picture: asks for a newer frame every 400 ms (an unchanged page costs a 204). */
function useLiveFrame(runId: string, on: boolean): { src: string | null; url: string } {
  const [frame, setFrame] = useState<{ src: string | null; url: string }>({ src: null, url: '' })
  const seq = useRef(-1)
  useEffect(() => {
    if (!on) return
    let stopped = false
    let timer: number | undefined
    let current: string | null = null
    const tick = async () => {
      try {
        const r = await fetch(explorationLiveUrl(runId, seq.current), { credentials: 'include' })
        if (r.status === 200) {
          const blob = await r.blob()
          if (stopped) return  // this view has gone: the frame is for the next one to fetch
          seq.current = Number(r.headers.get('X-Frame-Seq') ?? seq.current)
          const src = URL.createObjectURL(blob)
          if (current) URL.revokeObjectURL(current)
          current = src
          setFrame({ src, url: r.headers.get('X-Frame-Url') ?? '' })
        }
      } catch { /* the next tick tries again */ }
      if (!stopped) timer = window.setTimeout(tick, 400)
    }
    tick()
    return () => { stopped = true; if (timer) window.clearTimeout(timer); if (current) URL.revokeObjectURL(current) }
  }, [runId, on])
  return frame
}

export default function AgentBrowser({ run, compact = false }: { run: ExplorationRun; compact?: boolean }) {
  const running = run.status === 'running'
  const { w: W, h: H } = run.conditions?.device === 'phone' ? PHONE : DESKTOP
  const live = useLiveFrame(run.id, running)
  const framed = run.steps.filter((s) => s.frame)
  const timed = run.steps.filter((s) => typeof s.t === 'number')
  const [picked, setPicked] = useState<number | null>(null)  // while running: an earlier picture; null = live
  const mode: 'live' | 'video' | 'steps' = running ? (picked === null ? 'live' : 'steps') : run.video ? 'video' : 'steps'

  // The recording.
  const video = useRef<HTMLVideoElement>(null)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const lastT = timed.length ? timed[timed.length - 1].t! : 0
  const length = duration && Number.isFinite(duration) ? duration : lastT + 2
  const onScreen = [...timed].reverse().find((s) => s.t! <= time + 0.05) ?? timed[0]

  // The pictures, one after another.
  const [still, setStill] = useState(framed.length - 1)
  const at = picked ?? still
  useEffect(() => { if (!running && picked === null) setStill(framed.length - 1) }, [framed.length, running])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (mode !== 'steps' || !playing) return
    const id = window.setInterval(() => {
      const next = Math.min(framed.length - 1, at + 1)
      if (running) setPicked(next); else setStill(next)
      if (next >= framed.length - 1) setPlaying(false)
    }, STILL_MS / speed)
    return () => window.clearInterval(id)
  }, [mode, playing, at, speed, framed.length, running])

  useEffect(() => { if (video.current) video.current.playbackRate = speed }, [speed, mode])
  useEffect(() => { setPlaying(false) }, [mode])

  const step: Step | undefined = mode === 'video' ? onScreen : mode === 'steps' ? framed[at] : run.steps[run.steps.length - 1]
  const showBox = step?.box && (mode === 'steps' || (mode === 'video' && time - step.t! < BOX_SECONDS && time >= step.t! - 0.05))
  const url = mode === 'live' ? live.url || run.start_url : step?.url ?? run.start_url

  function togglePlay() {
    if (mode === 'video' && video.current) {
      if (video.current.paused) {
        if (video.current.ended || time >= length - 0.1) video.current.currentTime = 0
        video.current.play().catch(() => {})
      } else video.current.pause()
    } else if (mode === 'steps') {
      if (!playing && at >= framed.length - 1) { if (running) setPicked(0); else setStill(0) }
      setPlaying(!playing)
    }
  }
  function jump(by: -1 | 1) {
    if (mode === 'video' && video.current) {
      const i = onScreen ? timed.indexOf(onScreen) : -1
      const target = timed[Math.max(0, Math.min(timed.length - 1, i + by))]
      if (target) { video.current.currentTime = target.t!; setTime(target.t!) }
    } else {
      const next = Math.max(0, Math.min(framed.length - 1, at + by))
      if (running) setPicked(next); else setStill(next)
    }
  }
  const state = mode === 'live' ? null : mode === 'video' ? 'Recording' : running ? 'Earlier step' : 'Steps'

  return (
    <section aria-label="Mirmera's browser" style={{
      border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden', background: 'var(--panel)',
      width: '100%', maxWidth: run.conditions?.device === 'phone' ? 240 : 360,  // beside the session's Browser/API tools; a progress card's preview
    }}>
      {!compact && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '8px 10px 6px' }}>
          <Bot size={14} aria-hidden style={{ color: running ? 'var(--info)' : 'var(--text-tertiary)' }} />
          <h3 style={{ fontSize: 12.5, fontWeight: 600, margin: 0, flex: 1 }}>Mirmera's browser</h3>
          {mode === 'live' ? (
            <span role="status" aria-label="Live" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10.5, fontWeight: 600, color: 'var(--danger)' }}>
              <span className="mm-pulse" style={{ width: 7, height: 7, borderRadius: 4, background: 'var(--danger)' }} />LIVE
            </span>
          ) : <span style={{ fontSize: 10.5, color: 'var(--text-tertiary)' }}>{state}</span>}
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: compact ? '5px 8px' : '0 10px 7px', borderBottom: '1px solid var(--border)' }}>
        {!compact && <span aria-hidden style={{ display: 'flex', gap: 5 }}>
          {['#ff5f57', '#febc2e', '#28c840'].map((c) => <span key={c} style={{ width: 9, height: 9, borderRadius: 5, background: c, opacity: 0.8 }} />)}
        </span>}
        <span className="mono" aria-label="Address" title={url} style={{
          flex: 1, minWidth: 0, fontSize: 11, color: 'var(--text-secondary)', background: 'var(--panel-alt)', borderRadius: 6,
          padding: '3px 8px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{url}</span>
        {compact && mode === 'live' && <span role="status" aria-label="Live" className="mm-pulse" style={{ width: 7, height: 7, borderRadius: 4, background: 'var(--danger)' }} />}
      </div>

      <div style={{ position: 'relative', aspectRatio: `${W} / ${H}`, background: '#fff' }}>
        {mode === 'live' ? (live.src
          ? <img src={live.src} alt="Mirmera's browser, live" style={fill} />
          : <Blank text="Opening the app…" />
        ) : mode === 'video' ? (
          <video ref={video} src={explorationVideoUrl(run.id)} preload="metadata" playsInline muted aria-label="Recording of Mirmera's run"
            style={{ ...fill, background: '#fff' }}
            onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)} onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
            onDurationChange={(e) => setDuration(e.currentTarget.duration)}
            onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />
        ) : step ? (
          <img src={explorationStepFrameUrl(run.id, step.n)} alt={`Step ${step.n}: ${step.action}`} style={fill} />
        ) : <Blank text={running ? 'Opening the app…' : 'No pictures were kept for this run.'} />}
        {showBox && step?.box && (
          <div aria-label="Where it acted" style={{
            position: 'absolute', left: `${(step.box[0] / W) * 100}%`, top: `${(step.box[1] / H) * 100}%`,
            width: `${(step.box[2] / W) * 100}%`, height: `${(step.box[3] / H) * 100}%`,
            border: '2px solid var(--danger)', borderRadius: 4, boxShadow: '0 0 0 3px rgba(255, 80, 80, 0.25)', pointerEvents: 'none',
          }} />
        )}
      </div>

      {step && !compact && (
        <div aria-label="Step on screen" style={{ padding: '7px 10px', borderTop: '1px solid var(--border)', fontSize: 12 }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)', marginRight: 8 }}>Step {step.n}</span>
          {step.by === 'Mirmera' && <span title={step.kind === 'tour' ? 'After an accepted submit, Mirmera opened the answer page directly, refreshed it, went back, sent the form twice quickly, made its request fail, and did it with the keyboard only' : step.kind === 'confirm' ? "When the run ended, Mirmera replayed each new draft's steps in fresh browsers" : step.kind === 'access' ? "Mirmera opened this user's own pages signed out and as the second user" : 'The AI was stuck, so Mirmera took this step itself'} style={{ fontSize: 10.5, marginRight: 6, padding: '1px 6px', borderRadius: 8, background: 'var(--accent-bg)', color: 'var(--accent)' }}>{step.kind === 'tour' ? "Mirmera's flow tour" : step.kind === 'confirm' ? "Mirmera's replay" : step.kind === 'access' ? "Mirmera: who sees what" : 'Mirmera stepped in'}</span>}
          <span>{step.action}</span>
          {step.status !== 'ok' && <span style={{ color: 'var(--warning)' }}> ({step.status})</span>}
          {step.detail && <span style={{ color: 'var(--text-secondary)' }}> → {step.detail}</span>}
          {step.thought && <div style={{ color: 'var(--text-tertiary)', fontSize: 12, marginTop: 3 }}>{step.thought}</div>}
        </div>
      )}

      {!compact && running && <PairBox run={run} />}

      {!compact && (mode === 'video' || framed.length > 0) && (
        <div role="group" aria-label="Player" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px 8px', flexWrap: 'wrap' }}>
          {mode !== 'live' && (
            <button aria-label={playing ? 'Pause' : 'Play'} onClick={togglePlay} style={{ ...navButton, color: 'var(--accent)' }}>
              {playing ? <Pause size={13} /> : <Play size={13} />}
            </button>
          )}
          <button aria-label="Previous step" onClick={() => mode === 'live' ? setPicked(Math.max(0, framed.length - 2)) : jump(-1)} style={navButton}><ChevronLeft size={14} /></button>
          {mode === 'video' ? (
            <input type="range" aria-label="Position" min={0} max={length} step={0.1} value={Math.min(time, length)}
              onChange={(e) => { const v = Number(e.target.value); if (video.current) video.current.currentTime = v; setTime(v) }} style={{ flex: 1, minWidth: 80 }} />
          ) : (
            <input type="range" aria-label="Step" min={0} max={Math.max(0, framed.length - 1)} value={mode === 'live' ? framed.length - 1 : at}
              onChange={(e) => { const v = Number(e.target.value); if (running) setPicked(v); else setStill(v) }} style={{ flex: 1, minWidth: 80 }} />
          )}
          <button aria-label="Next step" onClick={() => jump(1)} disabled={mode === 'live'} style={navButton}><ChevronRight size={14} /></button>
          {mode !== 'live' && (
            <button aria-label="Speed" title="Playback speed" onClick={() => setSpeed(SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length])}
              style={{ ...navButton, width: 'auto', padding: '0 7px', fontSize: 11 }}>{speed}×</button>
          )}
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
            {mode === 'video' ? `${clockText(time)} / ${clockText(length)}` : mode === 'steps' ? `${at + 1} / ${framed.length}` : ''}
          </span>
          {running && picked !== null && (
            <button onClick={() => { setPicked(null); setPlaying(false) }} style={{ ...navButton, width: 'auto', padding: '2px 8px', fontSize: 11.5, color: 'var(--danger)' }}>Back to live</button>
          )}
        </div>
      )}

      {!compact && !running && run.har && (
        <div style={{ padding: '0 10px 8px', fontSize: 11.5 }}>
          <a href={explorationHarUrl(run.id)} download style={{ color: 'var(--accent)', display: 'inline-flex', gap: 5, alignItems: 'center' }}
            title="Every request the browser made, for DevTools or a developer. Cookies, authorization, passwords, tokens and pictures are taken out.">
            <Download size={12} aria-hidden />Network log (HAR)
          </a>
        </div>
      )}
    </section>
  )
}

/** Pair with the agent: what to try next, read before its next step. */
function PairBox({ run }: { run: ExplorationRun }) {
  const [text, setText] = useState('')
  const [sent, setSent] = useState<NonNullable<ExplorationRun['hints']>>([])
  const [error, setError] = useState<string | null>(null)
  const hints = [...(run.hints ?? []), ...sent.filter((s) => !(run.hints ?? []).some((h) => h.id === s.id))]
  async function send() {
    const t = text.trim()
    if (t.length < 2) return
    setError(null)
    try {
      const updated = await api.hintExploration(run.id, t)
      setSent(updated.hints ?? [])
      setText('')
    } catch (e) { setError(errorText(e)) }
  }
  return (
    <div aria-label="Pair with Mirmera" role="group" style={{ padding: '8px 10px', borderTop: '1px solid var(--border)' }}>
      {hints.slice(-3).map((h) => (
        <div key={h.id} style={{ fontSize: 11.5, marginBottom: 4, color: h.by === 'Mirmera' ? 'var(--text-tertiary)' : 'var(--text-secondary)' }}>
          <b style={{ fontWeight: 600 }}>{h.by}:</b> {h.text}{' '}
          <span style={{ color: 'var(--text-quiet)' }}>{h.step ? `· taken at step ${h.step}` : '· waiting for its next step'}</span>
        </div>
      ))}
      <form onSubmit={(e) => { e.preventDefault(); send() }} style={{ display: 'flex', gap: 6 }}>
        <input aria-label="Tell Mirmera what to try next" value={text} onChange={(e) => setText(e.target.value)} maxLength={400}
          placeholder="Tell Mirmera what to try next…" style={{
            flex: 1, minWidth: 0, fontSize: 12, padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)',
            background: 'var(--panel-alt)', color: 'var(--text)', fontFamily: 'var(--font-ui)',
          }} />
        <button type="submit" disabled={text.trim().length < 2} style={{ ...navButton, width: 'auto', padding: '0 10px', fontSize: 11.5, color: 'var(--accent)' }}>Send</button>
      </form>
      {error && <div role="alert" style={{ fontSize: 11.5, color: 'var(--danger)', marginTop: 4 }}>{error}</div>}
    </div>
  )
}

function Blank({ text }: { text: string }) {
  return <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', fontSize: 12.5, color: '#666', background: 'var(--panel-alt)' }}>{text}</div>
}

const fill: React.CSSProperties = { width: '100%', height: '100%', display: 'block', objectFit: 'contain' }

const navButton: React.CSSProperties = {
  width: 26, height: 24, display: 'grid', placeItems: 'center', borderRadius: 6, border: '1px solid var(--border)',
  background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer',
}
