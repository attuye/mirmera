/** Feedback loop UI: a session's suggested risk re-scores (on the debrief),
 *  and a risk's score history (on the Risks page). */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useProjectPath } from '../project'
import { api, RiskChange, SessionRiskFeedback } from '../api/client'
import { scoreBand } from './RiskMatrix'
import { utcDate } from './Evidence'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

const LEVELS = [1, 2, 3, 4, 5]

function Score({ impact, likelihood }: { impact: number; likelihood: number }) {
  const band = scoreBand(impact * likelihood)
  return (
    <span className="mono" style={{ fontSize: 12 }}>
      I{impact}×L{likelihood} <span style={{ color: band.fg, fontWeight: 600 }}>= {impact * likelihood}</span>
    </span>
  )
}

/** "I5×L2 = 10 → I5×L3 = 15", with the direction marked. */
export function ScoreChange({ from, to }: { from: [number, number]; to: [number, number] }) {
  const delta = to[0] * to[1] - from[0] * from[1]
  return (
    <span style={{ display: 'inline-flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
      <Score impact={from[0]} likelihood={from[1]} />
      <span aria-hidden style={{ color: delta > 0 ? 'var(--danger)' : delta < 0 ? 'var(--accent)' : 'var(--text-tertiary)' }}>
        {delta > 0 ? '▲' : delta < 0 ? '▼' : '→'}
      </span>
      <Score impact={to[0]} likelihood={to[1]} />
    </span>
  )
}

function Evidence({ lines }: { lines: string[] }) {
  if (!lines.length) return null
  return (
    <div style={{ marginTop: 6 }}>
      {lines.map((line, i) => (
        <div key={i} className="mono" style={{ fontSize: 11.5, color: 'var(--text-secondary)', wordBreak: 'break-word', borderLeft: '2px solid var(--border)', paddingLeft: 8, marginTop: 2 }}>
          {line}
        </div>
      ))}
    </div>
  )
}

function SuggestionCard({ change, onDecided }: { change: RiskChange; onDecided: (c: RiskChange) => void }) {
  const [impact, setImpact] = useState(change.to_impact)
  const [likelihood, setLikelihood] = useState(change.to_likelihood)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const stale = change.status === 'proposed' &&
    (change.current_impact !== change.from_impact || change.current_likelihood !== change.from_likelihood)

  async function decide(kind: 'accept' | 'reject') {
    setBusy(true)
    setError(null)
    try {
      const decision = { note: note.trim() || undefined, ...(kind === 'accept' ? { impact, likelihood } : {}) }
      onDecided(await (kind === 'accept' ? api.acceptRiskChange : api.rejectRiskChange)(change.id, decision))
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const select = (label: string, value: number, set: (n: number) => void) => (
    <label style={{ width: 96 }}>
      <span style={fieldLabel}>{label}</span>
      <select aria-label={label} value={value} onChange={(e) => set(Number(e.target.value))} style={input}>
        {LEVELS.map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
    </label>
  )

  return (
    <li aria-label={`${change.risk_ref} ${change.risk_title}`} style={{
      listStyle: 'none', background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 10,
      padding: '12px 14px', marginBottom: 8,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>
          <span className="mono" style={{ color: 'var(--text-tertiary)', marginRight: 6 }}>{change.risk_ref}</span>
          {change.risk_title}
        </span>
        <ScoreChange from={[change.from_impact, change.from_likelihood]} to={[change.to_impact, change.to_likelihood]} />
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>{change.reason}</div>
      <Evidence lines={change.evidence} />

      {change.status === 'proposed' && !stale && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', marginTop: 12, borderTop: '1px solid var(--border-soft)', paddingTop: 12 }}>
          {select('Impact', impact, setImpact)}
          {select('Likelihood', likelihood, setLikelihood)}
          <label style={{ flex: 1, minWidth: 180 }}>
            <span style={fieldLabel}>Your note (optional)</span>
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why you agree or not" style={input} />
          </label>
          <button onClick={() => decide('reject')} disabled={busy} style={quietButton}>Reject</button>
          <button onClick={() => decide('accept')} disabled={busy} style={primaryButton(busy)}>
            {busy ? 'Saving…' : (impact !== change.to_impact || likelihood !== change.to_likelihood) ? 'Accept my score' : 'Accept'}
          </button>
        </div>
      )}
      {stale && (
        <div role="status" style={{ fontSize: 12, color: 'var(--warning)', marginTop: 10 }}>
          {change.risk_ref} was re-scored since this suggestion (now I{change.current_impact}×L{change.current_likelihood}). Ask for suggestions again.
        </div>
      )}
      {(change.status === 'accepted' || change.status === 'rejected') && (
        <div role="status" style={{ fontSize: 12.5, marginTop: 10, color: change.status === 'accepted' ? 'var(--accent)' : 'var(--text-secondary)' }}>
          {change.status === 'accepted'
            ? <>Accepted: now <Score impact={change.final_impact!} likelihood={change.final_likelihood!} /></>
            : 'Rejected: score unchanged'}
          {change.tester_note && <span style={{ color: 'var(--text-secondary)' }}>. “{change.tester_note}”</span>}
        </div>
      )}
      <ErrorLine message={error} />
    </li>
  )
}

/** Debrief section: ask for suggestions, then decide on each one. */
export function SessionRiskUpdates({ sessionId }: { sessionId: string }) {
  const path = useProjectPath()
  const [feedback, setFeedback] = useState<SessionRiskFeedback | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.getRiskUpdates(sessionId).then(setFeedback).catch((e) => setError(errorText(e))) }, [sessionId])

  async function ask() {
    setBusy(true)
    setError(null)
    try {
      setFeedback(await api.suggestRiskUpdates(sessionId))
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  if (!feedback) return <ErrorLine message={error} />
  if (feedback.risk_count === 0) {
    return <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)' }}>This charter has no linked risks, so there are no scores to update.</div>
  }
  const decisions = feedback.changes.filter((c) => c.status !== 'no_change')
  const unchanged = feedback.changes.filter((c) => c.status === 'no_change')
  const asked = feedback.changes.length > 0

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <button onClick={ask} disabled={busy} style={asked ? quietButton : primaryButton(busy)}>
          {busy ? 'Thinking…' : asked ? 'Ask again' : 'Suggest risk updates'}
        </button>
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
          Based on this session's notes, bugs and browser recording. You decide each change.
        </span>
      </div>
      {feedback.unreviewed_bugs > 0 && (
        <div style={{ fontSize: 12, color: 'var(--warning)', marginBottom: 10 }}>
          {feedback.unreviewed_bugs} bug{feedback.unreviewed_bugs === 1 ? '' : 's'} from this session not reviewed yet.
          Suggestions are better once each is saved as a bug or a question: <Link to={path('/bugs')}>review bugs</Link>.
        </div>
      )}
      {feedback.warnings.map((w, i) => <div key={i} style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>{w}</div>)}
      <ul style={{ margin: 0, padding: 0 }}>
        {decisions.map((c) => (
          <SuggestionCard key={c.id} change={c}
            onDecided={(d) => setFeedback((f) => f && { ...f, changes: f.changes.map((x) => (x.id === d.id ? d : x)) })} />
        ))}
      </ul>
      {unchanged.length > 0 && (
        <div aria-label="No change suggested" role="list" style={{ marginTop: 6 }}>
          {unchanged.map((c) => (
            <div role="listitem" key={c.id} style={{ fontSize: 12.5, color: 'var(--text-secondary)', padding: '4px 0' }}>
              <span className="mono" style={{ color: 'var(--text-tertiary)' }}>{c.risk_ref}</span> no change: {c.reason}
            </div>
          ))}
        </div>
      )}
      <ErrorLine message={error} />
    </div>
  )
}

const HISTORY_LABEL: Record<RiskChange['status'], string> = {
  accepted: 'Accepted', rejected: 'Rejected', proposed: 'Suggested, waiting for a decision', no_change: 'Looked at, no change',
}

/** Risks page: how a risk's score changed, and why. Loaded when opened. */
export function RiskHistory({ riskId, version }: { riskId: string; version: string }) {
  const path = useProjectPath()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<RiskChange[] | null>(null)

  useEffect(() => {
    if (open) api.riskHistory(riskId).then(setItems).catch(() => setItems([]))
  }, [open, riskId, version])

  return (
    <div style={{ marginTop: 8 }}>
      <button onClick={() => setOpen(!open)} aria-expanded={open} style={{ ...quietButton, padding: '3px 9px', fontSize: 11.5 }}>
        {open ? 'Hide history' : 'History'}
      </button>
      {open && items && (
        <ol aria-label="Score history" style={{ margin: '8px 0 0', padding: 0 }}>
          {items.length === 0 && <li style={{ listStyle: 'none', fontSize: 12, color: 'var(--text-tertiary)' }}>No score changes yet.</li>}
          {items.map((c) => {
            const to: [number, number] = c.final_impact ? [c.final_impact, c.final_likelihood!] : [c.to_impact, c.to_likelihood]
            return (
              <li key={c.id} style={{ listStyle: 'none', fontSize: 12, padding: '6px 0', borderTop: '1px solid var(--border-soft)' }}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
                  <span className="mono" style={{ color: 'var(--text-quiet)', fontSize: 11 }}>{utcDate(c.created_at).toLocaleDateString()}</span>
                  <ScoreChange from={[c.from_impact, c.from_likelihood]} to={to} />
                  <span style={{ color: 'var(--text-secondary)' }}>
                    {c.source === 'tester' ? 'Re-scored by you' : HISTORY_LABEL[c.status]}
                    {c.source === 'ai' && c.session_id && <> · <Link to={path(`/sessions/${c.session_id}/debrief`)}>session</Link></>}
                  </span>
                </div>
                {c.source === 'ai' && c.reason && <div style={{ color: 'var(--text-tertiary)', marginTop: 2 }}>{c.reason}</div>}
                {c.tester_note && <div style={{ color: 'var(--text-secondary)', marginTop: 2 }}>Your note: {c.tester_note}</div>}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
