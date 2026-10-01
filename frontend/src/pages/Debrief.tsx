import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  api, Charter, CharterStatus, Debrief, ObservedRisk, OutlookItem, QUALITY_CRITERIA, SessionRecord,
} from '../api/client'
import Sidebar from '../components/Sidebar'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from '../components/ui'
import { RecordingFiles, SIGNAL_KINDS, countLine } from '../components/Evidence'
import type { Evidence, EvidenceKind } from '../api/client'

// PROOF debrief (Session-Based Test Management): Past, Results, Obstacles,
// Outlook, Feelings. The AI drafts the first four from the session log; the
// tester edits them and is the only one who writes Feelings.

const STATUS: Record<CharterStatus, { label: string; color: string }> = {
  fulfilled: { label: 'Charter fulfilled', color: 'var(--accent)' },
  partially_fulfilled: { label: 'Charter partly fulfilled', color: 'var(--warning)' },
  not_fulfilled: { label: 'Charter not fulfilled', color: 'var(--danger)' },
}

const PHASES: { key: string; label: string; color: string }[] = [
  { key: 'on_charter', label: 'On charter', color: 'var(--accent)' },
  { key: 'bug_investigation', label: 'Bug investigation', color: 'var(--danger)' },
  { key: 'opportunity', label: 'Opportunity', color: 'var(--warning)' },
  { key: 'setup', label: 'Setup', color: 'var(--text-tertiary)' },
]

const panel: React.CSSProperties = {
  background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 10, padding: '16px 18px',
}
const textarea: React.CSSProperties = { ...input, lineHeight: 1.6, resize: 'vertical' }

const lines = (text: string) => text.split('\n').map((l) => l.trim()).filter(Boolean)
const utc = (iso: string) => new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime()

function sessionLength(s: SessionRecord): string | null {
  if (!s.ended_at) return null
  const total = Math.max(0, Math.floor((utc(s.ended_at) - utc(s.started_at)) / 1000 - s.total_paused_seconds))
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), sec = total % 60
  return h ? `${h} h ${m} min` : m ? `${m} min ${sec} s` : `${sec} s`
}

/** One PROOF section: a large letter, a heading, and a hint about who writes it. */
function Section({ letter, title, hint, children }: { letter: string; title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} style={{ display: 'grid', gridTemplateColumns: '44px 1fr', gap: 14, marginTop: 26 }}>
      <div className="mono" aria-hidden style={{ fontSize: 30, fontWeight: 600, color: 'var(--accent)', lineHeight: 1 }}>{letter}</div>
      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>{title}</h2>
          {hint && <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{hint}</span>}
        </div>
        {children}
      </div>
    </section>
  )
}

function TimeSplit({ breakdown }: { breakdown: Record<string, number> }) {
  const parts = PHASES.filter((p) => (breakdown[p.key] ?? 0) > 0)
  if (!parts.length) return <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)' }}>No notes logged, so no time split.</div>
  return (
    <div>
      <div role="img" aria-label="Time split" style={{ display: 'flex', height: 10, borderRadius: 5, overflow: 'hidden', background: 'var(--border)' }}>
        {parts.map((p) => <div key={p.key} style={{ width: `${breakdown[p.key] * 100}%`, background: p.color }} />)}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 18px', marginTop: 8 }}>
        {parts.map((p) => (
          <span key={p.key} className="mono" style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>
            <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: p.color, marginRight: 6 }} />
            {p.label} {Math.round(breakdown[p.key] * 100)}%
          </span>
        ))}
      </div>
    </div>
  )
}

function OutlookRow({ item, onSave }: { item: OutlookItem; onSave: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function save() {
    setBusy(true); setError(null)
    try { await onSave() } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return (
    <li aria-label={item.charter} style={{ ...panel, listStyle: 'none', padding: '12px 14px', marginBottom: 8 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, color: 'var(--text)' }}>{item.charter}</div>
          {item.reason && <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 3 }}>Why: {item.reason}</div>}
        </div>
        {item.charter_id
          ? <Link to="/charters/suggest" style={{ fontSize: 12, color: 'var(--accent)', whiteSpace: 'nowrap' }}>Saved as charter →</Link>
          : <button onClick={save} disabled={busy} style={{ ...quietButton, whiteSpace: 'nowrap' }}>{busy ? 'Saving…' : 'Save as charter'}</button>}
      </div>
      <ErrorLine message={error} />
    </li>
  )
}

function ObservedRiskRow({ risk, onPromote }: {
  risk: ObservedRisk
  onPromote: (p: { title: string; quality_criterion: string; impact: number; likelihood: number }) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(risk.text.slice(0, 80))
  const [criterion, setCriterion] = useState<string>('capability')
  const [impact, setImpact] = useState(3)
  const [likelihood, setLikelihood] = useState(3)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function promote() {
    setBusy(true); setError(null)
    try { await onPromote({ title: title.trim(), quality_criterion: criterion, impact, likelihood }) }
    catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const score = (label: string, value: number, set: (n: number) => void) => (
    <label style={{ width: 110 }}>
      <span style={fieldLabel}>{label}</span>
      <select aria-label={label} value={value} onChange={(e) => set(Number(e.target.value))} style={input}>
        {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
    </label>
  )

  return (
    <li aria-label={risk.text} style={{ ...panel, listStyle: 'none', padding: '12px 14px', marginBottom: 8 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, fontSize: 13 }}>{risk.text}</div>
        {risk.risk_id
          ? <Link to="/risks" style={{ fontSize: 12, color: 'var(--accent)', whiteSpace: 'nowrap' }}>Added to risks →</Link>
          : !open && <button onClick={() => setOpen(true)} style={{ ...quietButton, whiteSpace: 'nowrap' }}>Add as risk…</button>}
      </div>
      {open && !risk.risk_id && (
        <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <label style={{ display: 'block', marginBottom: 10 }}>
            <span style={fieldLabel}>Risk title</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} style={input} />
          </label>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <label style={{ width: 170 }}>
              <span style={fieldLabel}>Quality criterion</span>
              <select aria-label="Quality criterion" value={criterion} onChange={(e) => setCriterion(e.target.value)} style={input}>
                {QUALITY_CRITERIA.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            {score('Impact', impact, setImpact)}
            {score('Likelihood', likelihood, setLikelihood)}
            <span className="mono" style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '0 4px 9px' }}>score {impact * likelihood}</span>
            <div style={{ flex: 1 }} />
            <button onClick={() => setOpen(false)} style={quietButton}>Cancel</button>
            <button onClick={promote} disabled={busy || !title.trim()} style={primaryButton(busy || !title.trim())}>
              {busy ? 'Adding…' : 'Add to risk list'}
            </button>
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 8 }}>
            You scored it from what you saw, so it goes in as accepted, with tester evidence.
          </div>
        </div>
      )}
      <ErrorLine message={error} />
    </li>
  )
}

/** What the browser runner recorded during the session, with the trace and HAR. */
function RecordedEvidence({ items }: { items: Evidence[] }) {
  const counts: Partial<Record<EvidenceKind, number>> = {}
  for (const e of items) counts[e.kind] = (counts[e.kind] ?? 0) + e.count
  return (
    <section aria-label="Recorded evidence" style={{ ...panel, marginTop: 12, padding: '12px 18px' }}>
      <div style={{ display: 'flex', gap: 14, alignItems: 'baseline', flexWrap: 'wrap', marginBottom: 6 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600 }}>Recorded in the browser</span>
        <span className="mono" style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>
          {countLine(counts, [...SIGNAL_KINDS, 'navigation', 'screenshot']) || 'No signals'}
        </span>
      </div>
      <RecordingFiles items={items} />
    </section>
  )
}

export default function DebriefPage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const [debrief, setDebrief] = useState<Debrief | null>(null)
  const [session, setSession] = useState<SessionRecord | null>(null)
  const [charter, setCharter] = useState<Charter | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [evidence, setEvidence] = useState<Evidence[]>([])

  // Editable copies of the tester's text; saved together with one button.
  const [past, setPast] = useState('')
  const [results, setResults] = useState('')
  const [obstacles, setObstacles] = useState('')
  const [questions, setQuestions] = useState('')
  const [feelings, setFeelings] = useState('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    if (!sessionId) return
    api.getDebrief(sessionId).then((d) => {
      setDebrief(d)
      setPast(d.final_text ?? d.coverage_summary ?? '')
      setResults(d.results.join('\n'))
      setObstacles(d.obstacles.join('\n'))
      setQuestions(d.open_questions.join('\n'))
      setFeelings(d.tester_feelings ?? '')
    }).catch((e) => setLoadError(errorText(e)))
    api.getSession(sessionId).then((s) => {
      setSession(s)
      return api.getCharter(s.charter_id).then(setCharter)
    }).catch((e) => setLoadError(errorText(e)))
    api.listEvidence(sessionId).then(setEvidence).catch(console.error)
  }, [sessionId])

  const edit = (set: (v: string) => void) => (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    set(e.target.value); setDirty(true); setSaved(false)
  }

  async function save() {
    if (!sessionId) return
    setSaving(true); setSaveError(null)
    try {
      const d = await api.updateDebrief(sessionId, {
        final_text: past, results: lines(results), obstacles: lines(obstacles),
        open_questions: lines(questions), tester_feelings: feelings.trim() || null,
      })
      setDebrief(d); setDirty(false); setSaved(true)
    } catch (e) {
      setSaveError(errorText(e))
    } finally {
      setSaving(false)
    }
  }

  // Outlook and observed-risk actions save immediately and return the updated
  // debrief; only their link fields change, so the tester's unsaved text stays.
  const applyLinks = (d: Debrief) => setDebrief((cur) => cur && { ...cur, outlook: d.outlook, risks_observed: d.risks_observed })

  if (!debrief) {
    return (
      <div style={{ display: 'flex', minHeight: '100vh' }}>
        <Sidebar />
        <div style={{ padding: 40, color: 'var(--text-secondary)' }}>
          {loadError ? <ErrorLine message={loadError} /> : 'Loading debrief…'}
        </div>
      </div>
    )
  }

  const status = debrief.charter_status ? STATUS[debrief.charter_status] : null
  const length = session && sessionLength(session)

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ padding: '32px 40px 60px', maxWidth: 860, flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Session debrief</h1>
          {debrief.ai_provider && (
            <span className="mono" title={`${debrief.ai_provider} · ${debrief.ai_model ?? ''} · ${debrief.prompt_version ?? ''}`}
              style={{ fontSize: 11, color: 'var(--accent)', background: 'var(--accent-bg)', padding: '4px 10px', borderRadius: 7 }}>
              AI draft — review before saving
            </span>
          )}
        </div>
        {charter && <div style={{ fontSize: 13.5, color: 'var(--text-secondary)', marginTop: 8 }}>{charter.mission_statement}</div>}

        <div style={{ ...panel, marginTop: 18, display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 24 }}>
          <div>
            {status ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13.5, fontWeight: 600 }}>
                <span style={{ width: 9, height: 9, borderRadius: '50%', background: status.color }} />
                {status.label}
              </div>
            ) : <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>Charter status not drafted</div>}
            {debrief.charter_status_rationale && (
              <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 6 }}>{debrief.charter_status_rationale}</div>
            )}
            {length && <div className="mono" style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 10 }}>Session length {length} (pauses excluded)</div>}
          </div>
          <TimeSplit breakdown={debrief.time_breakdown} />
        </div>
        {evidence.length > 0 && <RecordedEvidence items={evidence} />}

        <Section letter="P" title="Past" hint="What happened, in your words">
          <textarea aria-label="Past" value={past} onChange={edit(setPast)} rows={6} style={textarea} />
        </Section>

        <Section letter="R" title="Results" hint="What you found or confirmed, one per line">
          <textarea aria-label="Results" value={results} onChange={edit(setResults)} rows={4} style={textarea} />
        </Section>

        <Section letter="O" title="Obstacles" hint="What got in the way of testing, one per line">
          <textarea aria-label="Obstacles" value={obstacles} onChange={edit(setObstacles)} rows={3} style={textarea} />
        </Section>

        <Section letter="O" title="Outlook" hint="What to test next">
          {debrief.outlook.length === 0
            ? <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)' }}>No follow-up charters suggested.</div>
            : <ul style={{ margin: 0, padding: 0 }}>
                {debrief.outlook.map((item, i) => (
                  <OutlookRow key={i} item={item} onSave={async () => applyLinks(await api.saveOutlookCharter(sessionId!, i))} />
                ))}
              </ul>}

          <div style={{ fontSize: 12.5, fontWeight: 600, margin: '16px 0 8px' }}>Risks noticed in this session</div>
          {debrief.risks_observed.length === 0
            ? <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)' }}>None suggested.</div>
            : <ul style={{ margin: 0, padding: 0 }}>
                {debrief.risks_observed.map((risk, i) => (
                  <ObservedRiskRow key={i} risk={risk}
                    onPromote={async (p) => applyLinks(await api.promoteObservedRisk(sessionId!, i, p))} />
                ))}
              </ul>}

          <label style={{ display: 'block', marginTop: 16 }}>
            <span style={{ ...fieldLabel, fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>Open questions</span>
            <textarea aria-label="Open questions" value={questions} onChange={edit(setQuestions)} rows={3} style={textarea}
              placeholder="One per line" />
          </label>
        </Section>

        <Section letter="F" title="Feelings" hint="Only you write this. The AI never drafts it.">
          <textarea aria-label="Feelings" value={feelings} onChange={edit(setFeelings)} rows={3} style={textarea}
            placeholder="How did the session feel? Confidence in what you covered, energy, anything that bugged you." />
        </Section>

        <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 26, marginLeft: 58 }}>
          <button onClick={save} disabled={saving} style={primaryButton(saving)}>
            {saving ? 'Saving…' : 'Save debrief'}
          </button>
          {saved && !dirty && <span role="status" style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>Saved ✓</span>}
          {dirty && <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Unsaved changes</span>}
          <div style={{ flex: 1 }} />
          <Link to="/debriefs" style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>All debriefs</Link>
        </div>
        <div style={{ marginLeft: 58 }}><ErrorLine message={saveError} /></div>
      </main>
    </div>
  )
}
