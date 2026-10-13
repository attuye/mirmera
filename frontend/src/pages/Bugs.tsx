import { useEffect, useState } from 'react'
import Sidebar from '../components/Sidebar'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton, ConfirmDelete } from '../components/ui'
import { api, BugFiling, BugListItem, BugReview, DISMISS_REASONS, DismissReason, Evidence, ORACLES, OracleHeuristic, Project, Reproducibility } from '../api/client'
import FileToTracker, { FilingLinks } from '../components/FileToTracker'
import RegressionTestPanel from '../components/RegressionTestPanel'
import ReproductionPanel from '../components/ReproductionPanel'
import BugStepsPanel from '../components/BugStepsPanel'
import IsolationPanel from '../components/IsolationPanel'
import { useProject, useProjectPath } from '../project'
import { Screenshot, SignalList } from '../components/Evidence'
import ErrorBoundary from '../components/ErrorBoundary'

const SEVERITY_COLOR: Record<string, string> = { high: 'var(--danger)', medium: 'var(--warning)', low: 'var(--text-tertiary)' }

const KIND_STYLE = {
  unreviewed: { label: 'Needs review', fg: 'var(--warning)', bg: 'var(--warning-bg)' },
  bug: { label: 'Bug', fg: 'var(--danger)', bg: 'var(--danger-bg)' },
  question: { label: 'Question', fg: 'var(--question)', bg: '#221C2B' },
  dismissed: { label: 'Dismissed', fg: 'var(--text-tertiary)', bg: 'var(--panel-alt)' },
}
type Filter = 'all' | 'unreviewed' | 'bug' | 'question' | 'dismissed'

const kindOf = (b: BugListItem): keyof typeof KIND_STYLE => b.dismissal ? 'dismissed' : b.kind ?? 'unreviewed'
const lines = (text: string) => text.split('\n').map((l) => l.trim()).filter(Boolean)

export default function Bugs() {
  const [bugs, setBugs] = useState<BugListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const { project, setProject } = useProject()

  useEffect(() => {
    setLoading(true)
    api.listAllBugs(project.id)
      .then(setBugs)
      .catch((e) => setLoadError(errorText(e)))
      .finally(() => setLoading(false))
  }, [project.id])

  function remove(bugId: string) {
    setBugs((prev) => prev.filter((b) => b.id !== bugId))
    setExpanded(null)
  }

  function replace(updated: BugListItem) {
    setBugs((prev) => prev.map((b) => (b.id === updated.id ? updated : b)))
  }

  const live = bugs.filter((b) => !b.dismissal)  // dismissed ones only under Dismissed
  const counts: Record<Filter, number> = {
    all: live.length,
    unreviewed: bugs.filter((b) => kindOf(b) === 'unreviewed').length,
    bug: bugs.filter((b) => kindOf(b) === 'bug').length,
    question: bugs.filter((b) => kindOf(b) === 'question').length,
    dismissed: bugs.filter((b) => kindOf(b) === 'dismissed').length,
  }
  const shown = filter === 'all' ? live : bugs.filter((b) => kindOf(b) === filter)

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 900, minWidth: 0 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Bugs</h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '6px 0 18px', maxWidth: 640, lineHeight: 1.5 }}>
          Bugs logged during sessions wait here for review. Draft a report with AI or write it yourself,
          then save it as a bug, which needs the oracle that makes it a problem, or as a question for the team.
        </p>

        <div role="tablist" aria-label="Filter bugs" style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
          {(['all', 'unreviewed', 'bug', 'question', 'dismissed'] as Filter[]).map((f) => (
            <button key={f} role="tab" aria-selected={filter === f} onClick={() => setFilter(f)} style={{
              ...quietButton,
              color: filter === f ? 'var(--text)' : 'var(--text-secondary)',
              borderColor: filter === f ? 'var(--text-secondary)' : 'var(--border)',
            }}>
              {{ all: 'All', unreviewed: 'Needs review', bug: 'Bugs', question: 'Questions', dismissed: 'Dismissed' }[f]} ({counts[f]})
            </button>
          ))}
        </div>

        {loading && <div style={{ color: 'var(--text-secondary)' }}>Loading…</div>}
        <ErrorLine message={loadError} />
        {!loading && !loadError && shown.length === 0 && (
          <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
            {bugs.length === 0 ? 'No bugs logged yet. Log one from an active session.' : 'Nothing here.'}
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {shown.map((b) => {
            const isOpen = expanded === b.id
            const kind = KIND_STYLE[kindOf(b)]
            return (
              <article key={b.id} aria-label={b.title} style={{ border: '1px solid var(--border)', borderRadius: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center' }}>
                <button
                  onClick={() => setExpanded(isOpen ? null : b.id)}
                  aria-expanded={isOpen}
                  style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flex: 1, minWidth: 0,
                    background: 'transparent', color: 'inherit', textAlign: 'left', padding: '13px 16px',
                  }}
                >
                  <span style={{ minWidth: 0 }}>
                    <span style={{ fontSize: 13.5, fontWeight: 500, display: 'block' }}>{b.title}</span>
                    <span style={{ fontSize: 11.5, color: 'var(--text-secondary)', marginTop: 4, display: 'block' }}>{b.charter_mission}</span>
                  </span>
                  <span style={{ display: 'flex', gap: 10, alignItems: 'center', flexShrink: 0 }}>
                    {(b.sightings?.length ?? 0) > 0 && (
                      <span title="Mirmera met this problem again in other charters or runs" style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>
                        seen {(b.sightings?.length ?? 0) + 1}×
                      </span>
                    )}
                    <span style={{ fontSize: 11.5, color: SEVERITY_COLOR[b.severity] }}>{b.severity}</span>
                    <span style={{ fontSize: 11, fontWeight: 600, color: kind.fg, background: kind.bg, padding: '3px 8px', borderRadius: 5 }}>
                      {kind.label}
                    </span>
                  </span>
                </button>
                {b.filings.length > 0 && <span style={{ paddingRight: 16 }}><FilingLinks filings={b.filings} /></span>}
                </div>
                {isOpen && (
                  <div style={{ padding: '0 16px 16px', borderTop: '1px solid var(--border-soft)' }}>
                    <ErrorBoundary what="this bug">
                      <BugDetail bug={b} onSaved={replace} onDeleted={() => remove(b.id)} project={project} onProject={setProject} />
                    </ErrorBoundary>
                  </div>
                )}
              </article>
            )
          })}
        </div>
      </main>
    </div>
  )
}

// ---------- detail: trail, report, review ----------

function BugDetail({ bug, onSaved, onDeleted, project, onProject }: {
  bug: BugListItem
  onSaved: (b: BugListItem) => void
  onDeleted: () => void
  project: Project
  onProject: (p: Project) => void
}) {
  const [editing, setEditing] = useState(false)
  const [drafting, setDrafting] = useState(false)
  const [testVersion, setTestVersion] = useState(0)  // remounts the regression panel after the agent makes a test
  const [error, setError] = useState<string | null>(null)

  async function draft() {
    setDrafting(true)
    setError(null)
    try {
      const updated = await api.draftBugReport(bug.id)
      onSaved({ ...updated, charter_mission: bug.charter_mission })
      setEditing(true)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setDrafting(false)
    }
  }

  if (bug.dismissal) return <Dismissed bug={bug} onSaved={onSaved} />
  return (
    <div>
      <Trail bug={bug} />
      <Confirmation bug={bug} />
      <Sightings bug={bug} />
      <BugEvidence bug={bug} />
      {editing ? (
        <ReviewForm bug={bug} onCancel={() => setEditing(false)} onSaved={(b) => { onSaved(b); setEditing(false) }}
          onRedraft={draft} drafting={drafting} />
      ) : bug.kind ? (
        <>
          <Report bug={bug} onEdit={() => setEditing(true)} />
          <FileToTracker bug={bug} project={project} onProject={onProject}
            onFiled={(f: BugFiling) => onSaved({ ...bug, filings: [...bug.filings, f] })} />
          <BugStepsPanel bug={bug} />
          <IsolationPanel bug={bug} />
          {bug.kind === 'bug' && <RegressionTestPanel key={testVersion} bug={bug} />}
        </>
      ) : (
        <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
          <button onClick={draft} disabled={drafting} style={primaryButton(drafting)}>
            {drafting ? 'Drafting…' : 'Draft report with AI'}
          </button>
          <button onClick={() => setEditing(true)} style={quietButton}>Write it myself</button>
        </div>
      )}
      {!editing && !bug.kind && <><BugStepsPanel bug={bug} /><IsolationPanel bug={bug} /></>}
      {!editing && <ReproductionPanel bug={bug} onTestCreated={() => setTestVersion((v) => v + 1)} />}
      <ErrorLine message={error} />
      {!editing && (
        <div style={{ marginTop: 18, paddingTop: 12, borderTop: '1px solid var(--border-soft)', display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          {bug.filings.length === 0 && <DismissForm bug={bug} onSaved={onSaved} />}
          <ConfirmDelete label="Delete bug" question="Delete this bug with its tests and runs? Filed issues stay in the tracker."
            onConfirm={async () => { await api.deleteBug(bug.id); onDeleted() }} />
        </div>
      )}
    </div>
  )
}

/** Not a bug, and why: Mirmera won't draft the same problem again in this project. */
function DismissForm({ bug, onSaved }: { bug: BugListItem; onSaved: (b: BugListItem) => void }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState<DismissReason>('by_design')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!open) return <button onClick={() => setOpen(true)} style={quietButton}>Dismiss…</button>
  async function save() {
    setBusy(true); setError(null)
    try {
      const updated = await api.dismissBug(bug.id, reason, note.trim())
      onSaved({ ...updated, charter_mission: bug.charter_mission, filings: bug.filings })
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return (
    <form aria-label="Dismiss" onSubmit={(e) => { e.preventDefault(); save() }}
      style={{ flex: 1, minWidth: 280, border: '1px solid var(--border)', borderRadius: 8, padding: 12 }}>
      <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginBottom: 8, lineHeight: 1.45 }}>
        Not a bug? Say why. Mirmera won't draft this problem again in {'this project'}
        {reason === 'cant_reproduce' ? ' (unless it shows again: then it comes back as a draft)' : ''}, and its AI is told why.
      </div>
      <label style={fieldLabel} htmlFor={`why-${bug.id}`}>Why</label>
      <select id={`why-${bug.id}`} value={reason} onChange={(e) => setReason(e.target.value as DismissReason)} style={{ ...input, marginBottom: 8 }}>
        {(Object.keys(DISMISS_REASONS) as DismissReason[]).map((r) => <option key={r} value={r}>{DISMISS_REASONS[r]}</option>)}
      </select>
      <label style={fieldLabel} htmlFor={`note-${bug.id}`}>Note for the team and Mirmera (optional)</label>
      <input id={`note-${bug.id}`} value={note} onChange={(e) => setNote(e.target.value)} style={input}
        placeholder="e.g. The confirmation is shown once, on purpose" />
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        <button type="submit" disabled={busy} style={primaryButton(busy)}>{busy ? 'Dismissing…' : 'Dismiss'}</button>
        <button type="button" onClick={() => setOpen(false)} style={quietButton}>Cancel</button>
      </div>
      <ErrorLine message={error} />
    </form>
  )
}

function Dismissed({ bug, onSaved }: { bug: BugListItem; onSaved: (b: BugListItem) => void }) {
  const d = bug.dismissal!
  const [error, setError] = useState<string | null>(null)
  return (
    <div role="status" style={{ marginTop: 14, fontSize: 13, lineHeight: 1.5 }}>
      <div><strong>{DISMISS_REASONS[d.reason]}</strong>{d.by ? `, said ${d.by}` : ''} on {new Date(d.at).toLocaleDateString()}.
        {d.note && <> "{d.note}"</>}</div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 12.5 }}>
        {d.reason === 'cant_reproduce' ? 'If Mirmera meets it again, it comes back as a draft.' : "Mirmera won't draft this problem again in this project."}
      </div>
      <button style={{ ...quietButton, marginTop: 8 }} onClick={async () => {
        try { const b = await api.undoDismissal(bug.id); onSaved({ ...b, charter_mission: bug.charter_mission, filings: bug.filings }) } catch (e) { setError(errorText(e)) }
      }}>Undo: a draft again</button>
      <ErrorLine message={error} />
    </div>
  )
}

/** The screenshot taken when the bug was logged, and what the page reported
 *  in the two minutes before. Only shown when the browser runner was on. */
function BugEvidence({ bug }: { bug: BugListItem }) {
  const [items, setItems] = useState<Evidence[] | null>(null)
  useEffect(() => { api.bugEvidence(bug.id).then(setItems).catch(() => setItems([])) }, [bug.id])
  if (!items?.length) return null
  const shots = items.filter((e) => e.kind === 'screenshot')
  const signals = items.filter((e) => e.kind !== 'screenshot')
  return (
    <section aria-label="Recorded evidence" style={{ marginTop: 14 }}>
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>Recorded by the browser</div>
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {shots.map((e) => <Screenshot key={e.id} e={e} width={260} />)}
        {signals.length > 0 && (
          <div style={{ flex: 1, minWidth: 260 }}>
            <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginBottom: 2 }}>In the 2 minutes before it was logged</div>
            <SignalList items={signals} />
          </div>
        )}
      </div>
    </section>
  )
}

/** Mirmera replayed the draft's steps in fresh browsers: a steady bug, or a one-off? */
function Confirmation({ bug }: { bug: BugListItem }) {
  const c = bug.confirmation
  if (!c) return null
  const all = c.runs > 0 && c.reproduced === c.runs
  const color = !c.runs ? 'var(--text-tertiary)' : all ? 'var(--danger)' : c.reproduced ? 'var(--warning)' : 'var(--text-secondary)'
  const why = c.results.find((r) => !r.reproduced)?.detail
  return (
    <section aria-label="Replayed by Mirmera" style={{ marginTop: 14, fontSize: 12.5 }}>
      <span style={{ color: 'var(--text-tertiary)' }}>Replayed by Mirmera in a fresh browser: </span>
      {c.runs ? (
        <span style={{ color }}>
          shown {c.reproduced} of {c.runs} {all ? '(every time)' : c.reproduced ? '(some times)' : '(not again: a one-off?)'}
        </span>
      ) : <span style={{ color }}>{c.note ?? 'no steps to replay'}</span>}
      {why && !all && <div style={{ color: 'var(--text-quiet)', fontSize: 11.5, marginTop: 2 }}>When it didn't show: {why}</div>}
    </section>
  )
}

/** Where else the exploration agent met this problem: one draft to review, every place it showed up. */
function Sightings({ bug }: { bug: BugListItem }) {
  const seen = bug.sightings ?? []
  if (!seen.length) return null
  return (
    <section aria-label="Seen again" style={{ marginTop: 14 }}>
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>
        Seen again {seen.length === 1 ? 'once' : `${seen.length} times`}: Mirmera added it here instead of drafting it twice
      </div>
      {seen.map((s, i) => (
        <div key={i} style={{ display: 'flex', gap: 10, fontSize: 12.5, marginBottom: 3 }}>
          <span className="mono" style={{ color: 'var(--text-quiet)', width: 72, flexShrink: 0, fontSize: 11.5 }}>
            {new Date(s.at.endsWith('Z') || s.at.includes('+') ? s.at : s.at + 'Z').toLocaleDateString()}
          </span>
          <span style={{ color: 'var(--text-secondary)' }}>{s.mission || 'Another charter'}</span>
        </div>
      ))}
    </section>
  )
}

function Trail({ bug }: { bug: BugListItem }) {
  const env = Object.entries(bug.environment)
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>What happened before it (session notes)</div>
      {bug.reproduction_trail.length === 0 && (
        <div style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>No notes were logged before this bug.</div>
      )}
      {bug.reproduction_trail.map((n, i) => (
        <div key={i} style={{ display: 'flex', gap: 10, fontSize: 12.5, marginBottom: 3 }}>
          <span className="mono" style={{ color: 'var(--text-quiet)', width: 72, flexShrink: 0, fontSize: 11.5 }}>
            {n.timestamp ? new Date(n.timestamp.endsWith('Z') ? n.timestamp : n.timestamp + 'Z').toLocaleTimeString() : n.tag}
          </span>
          <span style={{ color: 'var(--text-secondary)' }}>{n.content}</span>
        </div>
      ))}
      {env.length > 0 && (
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 8 }}>
          Environment: {env.map(([k, v]) => `${k} ${v}`).join(', ')}
        </div>
      )}
    </div>
  )
}

function ReportBlock({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 13, lineHeight: 1.55 }}>{children}</div>
    </div>
  )
}

function Report({ bug, onEdit }: { bug: BugListItem; onEdit: () => void }) {
  return (
    <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {bug.description && <ReportBlock label="Summary">{bug.description}</ReportBlock>}
      {bug.steps_to_reproduce.length > 0 && (
        <ReportBlock label="Steps to reproduce">
          <ol style={{ margin: 0, paddingLeft: 20 }}>{bug.steps_to_reproduce.map((s, i) => <li key={i}>{s}</li>)}</ol>
        </ReportBlock>
      )}
      {(bug.expected || bug.actual) && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <ReportBlock label="Expected">{bug.expected || '—'}</ReportBlock>
          <ReportBlock label="Actual">{bug.actual || '—'}</ReportBlock>
        </div>
      )}
      {bug.oracle_heuristic && (
        <ReportBlock label={`Why it's a problem: ${bug.oracle_heuristic.replace(/_/g, ' ')}`}>{bug.oracle_explanation}</ReportBlock>
      )}
      {bug.severity_rationale && <ReportBlock label={`Severity: ${bug.severity}`}>{bug.severity_rationale}</ReportBlock>}
      {bug.open_questions.length > 0 && (
        <ReportBlock label="Open questions">
          <ul style={{ margin: 0, paddingLeft: 20 }}>{bug.open_questions.map((q, i) => <li key={i}>{q}</li>)}</ul>
        </ReportBlock>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 11, color: 'var(--text-quiet)' }}>
          {[bug.last_change, bug.ai_draft ? `AI draft by ${bug.ai_provider}/${bug.ai_model} kept for comparison` : null]
            .filter(Boolean).join('. ')}
        </span>
        <button onClick={onEdit} style={quietButton}>Edit report</button>
      </div>
    </div>
  )
}

function ReviewForm({ bug, onCancel, onSaved, onRedraft, drafting }: {
  bug: BugListItem
  onCancel: () => void
  onSaved: (b: BugListItem) => void
  onRedraft: () => void
  drafting: boolean
}) {
  // Start from the tester's saved report if there is one, otherwise from the AI draft.
  const d = bug.ai_draft
  const fromDraft = !bug.kind && d
  const [kind, setKind] = useState<'bug' | 'question'>(bug.kind ?? d?.kind ?? 'bug')
  const [title, setTitle] = useState(fromDraft ? d.title : bug.title)
  const [summary, setSummary] = useState((fromDraft ? d.summary : bug.description) ?? '')
  const [steps, setSteps] = useState((fromDraft ? d.steps_to_reproduce : bug.steps_to_reproduce).join('\n'))
  const [expected, setExpected] = useState((fromDraft ? d.expected : bug.expected) ?? '')
  const [actual, setActual] = useState((fromDraft ? d.actual : bug.actual) ?? '')
  const [oracle, setOracle] = useState<OracleHeuristic | ''>((fromDraft ? d.oracle?.heuristic : bug.oracle_heuristic) ?? '')
  const [oracleWhy, setOracleWhy] = useState((fromDraft ? d.oracle?.explanation : bug.oracle_explanation) ?? '')
  const [severity, setSeverity] = useState(fromDraft ? d.severity : bug.severity)
  const [severityWhy, setSeverityWhy] = useState((fromDraft ? d.severity_rationale : bug.severity_rationale) ?? '')
  const [repro, setRepro] = useState<Reproducibility>((fromDraft ? d.reproducibility : bug.reproducibility) ?? 'unknown')
  const [questions, setQuestions] = useState((fromDraft ? d.open_questions : bug.open_questions).join('\n'))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const missingOracle = kind === 'bug' && (!oracle || !oracleWhy.trim())
  const libraryPath = useProjectPath()
  const canSave = title.trim().length >= 3 && !missingOracle && !saving

  async function save() {
    setSaving(true)
    setError(null)
    const review: BugReview = {
      kind, title: title.trim(), description: summary.trim() || null,
      steps_to_reproduce: lines(steps), expected: expected.trim() || null, actual: actual.trim() || null,
      oracle_heuristic: oracle || null,
      oracle_explanation: oracleWhy.trim() || null,
      severity, severity_rationale: severityWhy.trim() || null, reproducibility: repro,
      open_questions: lines(questions),
    }
    try {
      const updated = await api.reviewBug(bug.id, review)
      onSaved({ ...updated, charter_mission: bug.charter_mission })
    } catch (e) {
      setError(errorText(e))
    } finally {
      setSaving(false)
    }
  }

  const id = (name: string) => `${name}-${bug.id}`

  return (
    <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {d && (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
          <span>
            {fromDraft ? 'Pre-filled from the AI draft' : 'An AI draft exists'} ({bug.ai_provider}/{bug.ai_model}).
            Check every field against what you saw; it only knows your notes{bug.evidence.length || d.evidence_used?.length ? ' and what the browser recorded' : ''}.
          </span>
          <button onClick={onRedraft} disabled={drafting} style={quietButton}>{drafting ? 'Drafting…' : 'Redraft'}</button>
        </div>
      )}
      {d?.evidence_used && d.evidence_used.length > 0 && (
        <div role="note" aria-label="Browser evidence the draft used" style={{
          fontSize: 12, background: 'var(--panel-alt)', border: '1px solid var(--border)', borderRadius: 8, padding: '9px 12px',
        }}>
          <div style={{ color: 'var(--text-tertiary)', marginBottom: 4 }}>
            The draft relied on this browser evidence. Is it really connected to this bug?
          </div>
          {d.evidence_used.map((line, i) => (
            <div key={i} className="mono" style={{ fontSize: 11.5, color: 'var(--text-secondary)', wordBreak: 'break-word' }}>{line}</div>
          ))}
        </div>
      )}

      <fieldset style={{ border: 'none', padding: 0, margin: 0, display: 'flex', gap: 8 }}>
        <legend style={fieldLabel}>Save as</legend>
        {(['bug', 'question'] as const).map((k) => (
          <label key={k} style={{
            display: 'flex', gap: 7, alignItems: 'center', fontSize: 12.5, padding: '6px 11px', borderRadius: 7, cursor: 'pointer',
            border: `1px solid ${kind === k ? 'var(--accent)' : 'var(--border)'}`,
          }}>
            <input type="radio" name={id('kind')} checked={kind === k} onChange={() => setKind(k)} />
            {k === 'bug' ? 'Bug: something is wrong' : 'Question: not sure it is wrong'}
          </label>
        ))}
      </fieldset>

      <div>
        <label htmlFor={id('title')} style={fieldLabel}>Title</label>
        <input id={id('title')} value={title} onChange={(e) => setTitle(e.target.value)} style={input} />
      </div>
      <div>
        <label htmlFor={id('summary')} style={fieldLabel}>Summary</label>
        <textarea id={id('summary')} value={summary} onChange={(e) => setSummary(e.target.value)} rows={2} style={{ ...input, resize: 'vertical' }} />
      </div>
      <div>
        <label htmlFor={id('steps')} style={fieldLabel}>Steps to reproduce (one per line)</label>
        <textarea id={id('steps')} value={steps} onChange={(e) => setSteps(e.target.value)} rows={4} style={{ ...input, resize: 'vertical' }} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label htmlFor={id('expected')} style={fieldLabel}>Expected</label>
          <textarea id={id('expected')} value={expected} onChange={(e) => setExpected(e.target.value)} rows={2} style={{ ...input, resize: 'vertical' }} />
        </div>
        <div>
          <label htmlFor={id('actual')} style={fieldLabel}>Actual</label>
          <textarea id={id('actual')} value={actual} onChange={(e) => setActual(e.target.value)} rows={2} style={{ ...input, resize: 'vertical' }} />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: 12 }}>
        <div>
          <label htmlFor={id('oracle')} style={fieldLabel}>Oracle{kind === 'bug' ? ' (required for a bug)' : ''}</label>
          <select id={id('oracle')} value={oracle} onChange={(e) => setOracle(e.target.value as OracleHeuristic | '')} style={input}>
            <option value="">None yet</option>
            {(Object.keys(ORACLES) as OracleHeuristic[]).map((o) => (
              <option key={o} value={o}>{o.replace(/_/g, ' ')}: {ORACLES[o]}</option>
            ))}
          </select>
          <a href={libraryPath(`/heuristics${oracle ? `#${oracle}` : ''}`)} target="_blank" rel="noreferrer"
            style={{ fontSize: 11.5, color: 'var(--text-tertiary)', display: 'inline-block', marginTop: 4 }}>
            {oracle ? 'About this oracle ↗' : 'How to choose an oracle ↗'}
          </a>
        </div>
        <div>
          <label htmlFor={id('oracle-why')} style={fieldLabel}>Why it's a problem</label>
          <input id={id('oracle-why')} value={oracleWhy} onChange={(e) => setOracleWhy(e.target.value)}
            placeholder="e.g. The pricing spec says coupons survive currency changes" style={input} />
        </div>
      </div>
      {missingOracle && (
        <div style={{ fontSize: 12, color: 'var(--warning)' }}>
          A bug needs an oracle and a reason. If you can't say why it's wrong yet, save it as a question.
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '130px 160px 1fr', gap: 12 }}>
        <div>
          <label htmlFor={id('severity')} style={fieldLabel}>Severity</label>
          <select id={id('severity')} value={severity} onChange={(e) => setSeverity(e.target.value as typeof severity)} style={input}>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>
        <div>
          <label htmlFor={id('repro')} style={fieldLabel}>Reproducibility</label>
          <select id={id('repro')} value={repro} onChange={(e) => setRepro(e.target.value as Reproducibility)} style={input}>
            <option value="always">Always</option>
            <option value="intermittent">Intermittent</option>
            <option value="once">Seen once</option>
            <option value="unknown">Unknown</option>
          </select>
        </div>
        <div>
          <label htmlFor={id('severity-why')} style={fieldLabel}>Why this severity</label>
          <input id={id('severity-why')} value={severityWhy} onChange={(e) => setSeverityWhy(e.target.value)}
            placeholder="Who is affected, how badly, how often" style={input} />
        </div>
      </div>

      <div>
        <label htmlFor={id('questions')} style={fieldLabel}>Open questions (one per line)</label>
        <textarea id={id('questions')} value={questions} onChange={(e) => setQuestions(e.target.value)} rows={2} style={{ ...input, resize: 'vertical' }} />
      </div>

      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={save} disabled={!canSave} style={primaryButton(!canSave)}>
          {saving ? 'Saving…' : kind === 'bug' ? 'Save as bug' : 'Save as question'}
        </button>
        <button onClick={onCancel} style={quietButton}>Cancel</button>
      </div>
      <ErrorLine message={error} />
    </div>
  )
}
