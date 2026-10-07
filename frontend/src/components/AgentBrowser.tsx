/** Mirmera's browser: watch the exploration agent use the app, live while it works, then
 *  step through what it did. Each step shows the page as the agent saw it when it chose the
 *  step, with a box on the element it was about to use. */
import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { ExplorationRun, explorationLiveUrl, explorationStepFrameUrl } from '../api/client'

const W = 1280, H = 800  // the agent's fixed viewport (backend/app/agent/explore.py)

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
  const live = useLiveFrame(run.id, running)
  const framed = run.steps.filter((s) => s.frame)
  const [picked, setPicked] = useState<number | null>(null)  // index into framed; null = the latest
  const showLive = running && picked === null && live.src
  const at = picked ?? framed.length - 1
  const step = framed[at]
  const lastStep = run.steps[run.steps.length - 1]
  const caption = showLive ? lastStep : step
  const url = showLive ? live.url : step?.url ?? run.start_url

  return (
    <section aria-label="Mirmera's browser" style={{
      border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden', background: 'var(--panel)',
      width: '100%', maxWidth: 360,  // beside the session's Browser/API tools; a progress card's preview
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: compact ? '5px 8px' : '7px 10px', borderBottom: '1px solid var(--border)' }}>
        {!compact && <span aria-hidden style={{ display: 'flex', gap: 5 }}>
          {['#ff5f57', '#febc2e', '#28c840'].map((c) => <span key={c} style={{ width: 9, height: 9, borderRadius: 5, background: c, opacity: 0.8 }} />)}
        </span>}
        <span className="mono" title={url} style={{
          flex: 1, minWidth: 0, fontSize: 11, color: 'var(--text-secondary)', background: 'var(--panel-alt)', borderRadius: 6,
          padding: '3px 8px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{url}</span>
        {running && picked === null ? (
          <span role="status" aria-label="Live" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10.5, fontWeight: 600, color: 'var(--danger)' }}>
            <span className="mm-pulse" style={{ width: 7, height: 7, borderRadius: 4, background: 'var(--danger)' }} />LIVE
          </span>
        ) : framed.length > 0 && <span style={{ fontSize: 10.5, color: 'var(--text-tertiary)' }}>{running ? 'Earlier step' : 'Replay'}</span>}
      </div>

      <div style={{ position: 'relative', aspectRatio: `${W} / ${H}`, background: '#fff' }}>
        {showLive ? (
          <img src={live.src!} alt="Mirmera's browser, live" style={{ width: '100%', height: '100%', display: 'block', objectFit: 'contain' }} />
        ) : step ? (
          <>
            <img src={explorationStepFrameUrl(run.id, step.n)} alt={`Step ${step.n}: ${step.action}`}
              style={{ width: '100%', height: '100%', display: 'block', objectFit: 'contain' }} />
            {step.box && (
              <div aria-label="Where it acted" style={{
                position: 'absolute', left: `${(step.box[0] / W) * 100}%`, top: `${(step.box[1] / H) * 100}%`,
                width: `${(step.box[2] / W) * 100}%`, height: `${(step.box[3] / H) * 100}%`,
                border: '2px solid var(--danger)', borderRadius: 4, boxShadow: '0 0 0 3px rgba(255, 80, 80, 0.25)', pointerEvents: 'none',
              }} />
            )}
          </>
        ) : (
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', fontSize: 12.5, color: '#666', background: 'var(--panel-alt)' }}>
            {running ? 'Opening the app…' : 'No pictures were kept for this run.'}
          </div>
        )}
      </div>

      {caption && !compact && (
        <div style={{ padding: '7px 10px', borderTop: '1px solid var(--border)', fontSize: 12 }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)', marginRight: 8 }}>Step {caption.n}</span>
          <span>{caption.action}</span>
          {caption.status !== 'ok' && <span style={{ color: 'var(--warning)' }}> ({caption.status})</span>}
          {caption.detail && <span style={{ color: 'var(--text-secondary)' }}> → {caption.detail}</span>}
          {caption.thought && <div style={{ color: 'var(--text-tertiary)', fontSize: 12, marginTop: 3 }}>{caption.thought}</div>}
        </div>
      )}

      {!compact && framed.length > 0 && (
        <div role="group" aria-label="Steps" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px 8px' }}>
          <button aria-label="Previous step" disabled={at <= 0} onClick={() => setPicked(Math.max(0, at - 1))} style={navButton}><ChevronLeft size={14} /></button>
          <input type="range" aria-label="Step" min={0} max={framed.length - 1} value={at}
            onChange={(e) => setPicked(Number(e.target.value))} style={{ flex: 1 }} />
          <button aria-label="Next step" disabled={at >= framed.length - 1} onClick={() => setPicked(Math.min(framed.length - 1, at + 1))} style={navButton}><ChevronRight size={14} /></button>
          {running && picked !== null && (
            <button onClick={() => setPicked(null)} style={{ ...navButton, width: 'auto', padding: '2px 8px', fontSize: 11.5, color: 'var(--danger)' }}>Back to live</button>
          )}
        </div>
      )}
    </section>
  )
}

const navButton: React.CSSProperties = {
  width: 26, height: 24, display: 'grid', placeItems: 'center', borderRadius: 6, border: '1px solid var(--border)',
  background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer',
}
