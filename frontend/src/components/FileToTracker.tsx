/** File a reviewed bug to GitHub or Jira: check for duplicates first, then
 *  file a new issue or add this as a new occurrence to an existing one. */
import { useEffect, useState } from 'react'
import {
  api, BugFiling, BugListItem, DuplicateCandidate, DuplicateCheck, FilingPreview, Project, TrackerName, TrackerStatus,
} from '../api/client'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

const TRACKER_LABEL: Record<TrackerName, string> = { github: 'GitHub', jira: 'Jira' }

const VERDICT: Record<DuplicateCandidate['verdict'], { label: string; fg: string; bg: string }> = {
  likely_duplicate: { label: 'Likely duplicate', fg: 'var(--danger)', bg: 'var(--danger-bg)' },
  related: { label: 'Related', fg: 'var(--warning)', bg: 'var(--warning-bg)' },
  different: { label: 'Different', fg: 'var(--text-tertiary)', bg: 'var(--panel-alt)' },
  not_assessed: { label: 'Not assessed', fg: 'var(--text-secondary)', bg: 'var(--panel-alt)' },
}

export function FilingLinks({ filings }: { filings: BugFiling[] }) {
  if (!filings.length) return null
  return (
    <span style={{ display: 'inline-flex', gap: 6 }}>
      {filings.map((f) => (
        <a key={f.id} href={f.issue_url} target="_blank" rel="noreferrer"
          title={`${f.mode === 'new' ? 'Filed' : 'Added as an occurrence'} in ${TRACKER_LABEL[f.tracker]} ${f.target}`
            + (f.issue_status ? ` · ${f.issue_status} when last checked` : '')}
          className="mono" style={{ fontSize: 11, color: 'var(--info)', border: '1px solid var(--border)', padding: '2px 7px', borderRadius: 5,
            textDecoration: f.issue_closed ? 'line-through' : 'none' }}>
          {f.issue_key}{f.issue_closed ? ' closed' : ''} ↗
        </a>
      ))}
    </span>
  )
}

/** When a project has no repo / Jira project yet: set it here. Secrets stay in .env. */
function Setup({ status, project, onProject }: { status: TrackerStatus; project: Project; onProject: (p: Project) => void }) {
  const field = status.name === 'github' ? 'github_repo' : 'jira_project_key'
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const needsTarget = status.missing.some((m) => m.startsWith("the project's"))
  const envMissing = status.missing.filter((m) => !m.startsWith("the project's"))

  async function save() {
    setError(null)
    try {
      onProject(await api.updateProject(project.id, { [field]: value.trim() }))
    } catch (e) {
      setError(errorText(e))
    }
  }

  return (
    <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
      {needsTarget && (
        <form onSubmit={(e) => { e.preventDefault(); save() }} style={{ display: 'flex', gap: 8, alignItems: 'flex-end', margin: '4px 0 8px' }}>
          <label style={{ flex: 1, maxWidth: 320 }}>
            <span style={fieldLabel}>{status.name === 'github' ? 'GitHub repo for this project' : 'Jira project key for this project'}</span>
            <input value={value} onChange={(e) => setValue(e.target.value)} style={input}
              placeholder={status.name === 'github' ? 'owner/repo' : 'e.g. KYC'} />
          </label>
          <button type="submit" disabled={!value.trim()} style={primaryButton(!value.trim())}>Save</button>
        </form>
      )}
      {envMissing.length > 0 && (
        <div>
          Also needed on the server: <span className="mono" style={{ fontSize: 11.5 }}>{envMissing.join(', ')}</span>.
          Add {envMissing.length > 1 ? 'them' : 'it'} to backend/.env and restart the backend.
        </div>
      )}
      <ErrorLine message={error} />
    </div>
  )
}

function Candidate({ c, onPick, picked }: { c: DuplicateCandidate; onPick: () => void; picked: boolean }) {
  const v = VERDICT[c.verdict]
  return (
    <li aria-label={`${c.key} ${c.title}`} style={{
      listStyle: 'none', padding: '10px 12px', borderRadius: 8, marginBottom: 6,
      border: `1px solid ${picked ? 'var(--accent)' : 'var(--border)'}`, background: 'var(--panel-alt)',
    }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: v.fg, background: v.bg, padding: '2px 7px', borderRadius: 5 }}>{v.label}</span>
        <a href={c.url} target="_blank" rel="noreferrer" className="mono" style={{ fontSize: 12 }}>{c.key} ↗</a>
        <span style={{ fontSize: 13, flex: 1, minWidth: 200 }}>{c.title}</span>
        <span style={{ fontSize: 11.5, color: c.closed ? 'var(--text-tertiary)' : 'var(--text-secondary)' }}>{c.closed ? 'closed' : c.status}</span>
      </div>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>{c.reason}</div>
      {c.verdict !== 'different' && (
        <button onClick={onPick} style={{ ...quietButton, marginTop: 8, padding: '4px 10px', fontSize: 11.5 }}>
          {picked ? 'Selected' : `Add to ${c.key} as a new occurrence`}
        </button>
      )}
    </li>
  )
}

export default function FileToTracker({ bug, project, onProject, onFiled }: {
  bug: BugListItem
  project: Project
  onProject: (p: Project) => void
  onFiled: (f: BugFiling) => void
}) {
  const [statuses, setStatuses] = useState<TrackerStatus[] | null>(null)
  const [tracker, setTracker] = useState<TrackerName | null>(null)
  const [check, setCheck] = useState<DuplicateCheck | null>(null)
  const [preview, setPreview] = useState<FilingPreview | null>(null)
  const [showPreview, setShowPreview] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState<null | 'check' | 'file'>(null)
  const [error, setError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])

  useEffect(() => {
    api.trackerStatus(project.id).then((s) => {
      setStatuses(s)
      setTracker((t) => t ?? (s.find((x) => x.configured)?.name ?? s[0]?.name ?? null))
    }).catch((e) => setError(errorText(e)))
  }, [project.id, project.github_repo, project.jira_project_key])

  // A new tracker means a new search, a new preview and no pick.
  useEffect(() => {
    setCheck(null); setPicked(null); setPreview(null); setWarnings([])
    if (tracker) api.filingPreview(bug.id, tracker).then(setPreview).catch(() => setPreview(null))
  }, [tracker, bug.id, bug.last_change])

  const status = statuses?.find((s) => s.name === tracker)
  const filed = bug.filings.find((f) => f.tracker === tracker)

  async function runCheck() {
    if (!tracker) return
    setBusy('check'); setError(null); setPicked(null)
    try { setCheck(await api.checkDuplicates(bug.id, tracker)) } catch (e) { setError(errorText(e)) } finally { setBusy(null) }
  }

  async function file() {
    if (!tracker) return
    setBusy('file'); setError(null)
    try {
      const r = await api.fileBug(bug.id, {
        tracker, mode: picked ? 'comment' : 'new', issue_key: picked ?? undefined, note: note.trim() || undefined,
      })
      setWarnings(r.warnings)
      onFiled(r.filing)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(null)
    }
  }

  if (!statuses) return <ErrorLine message={error} />

  return (
    <section aria-label="File in tracker" style={{
      marginTop: 18, border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px', background: 'var(--panel)',
    }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <h3 style={{ fontSize: 13.5, fontWeight: 600, margin: 0 }}>File in your tracker</h3>
        <div role="tablist" aria-label="Tracker" style={{ display: 'flex', gap: 6 }}>
          {statuses.map((s) => (
            <button key={s.name} role="tab" aria-selected={tracker === s.name} onClick={() => setTracker(s.name)} style={{
              ...quietButton, padding: '4px 10px',
              color: tracker === s.name ? 'var(--text)' : 'var(--text-secondary)',
              borderColor: tracker === s.name ? 'var(--text-secondary)' : 'var(--border)',
            }}>
              {TRACKER_LABEL[s.name]}{s.target ? <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)' }}> {s.target}</span> : null}
            </button>
          ))}
        </div>
        {status?.is_mock && <span style={{ fontSize: 11, color: 'var(--warning)' }}>Mock trackers (MOCK_TRACKERS=true): nothing leaves this machine</span>}
      </div>

      {status && !status.configured && <Setup status={status} project={project} onProject={onProject} />}

      {status?.configured && filed && (
        <div role="status" style={{ fontSize: 13 }}>
          {filed.mode === 'new' ? 'Filed as' : 'Added as a new occurrence to'}{' '}
          <a href={filed.issue_url} target="_blank" rel="noreferrer" className="mono">{filed.issue_key} ↗</a>
          {filed.attachments.length > 0 && <span style={{ color: 'var(--text-secondary)' }}> with {filed.attachments.length} screenshot{filed.attachments.length > 1 ? 's' : ''} attached</span>}
          {warnings.map((w, i) => <div key={i} style={{ fontSize: 12, color: 'var(--warning)', marginTop: 4 }}>{w}</div>)}
        </div>
      )}

      {status?.configured && !filed && (
        <>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <button onClick={runCheck} disabled={!!busy} style={check ? quietButton : primaryButton(!!busy)}>
              {busy === 'check' ? 'Searching…' : check ? 'Search again' : 'Check for duplicates'}
            </button>
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
              {check
                ? <>Searched {TRACKER_LABEL[check.tracker]} for <span className="mono">{check.searched_for.join(', ') || '(no usable words in the title)'}</span>, open and closed.</>
                : 'Searches open and closed issues; the AI rates each match. You decide.'}
            </span>
          </div>

          {check && (
            <div style={{ marginTop: 10 }}>
              {check.candidates.length === 0
                ? <div style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>No matching issues found.</div>
                : <ul aria-label="Possible duplicates" style={{ margin: 0, padding: 0 }}>
                    {check.candidates.map((c) => (
                      <Candidate key={c.key} c={c} picked={picked === c.key}
                        onPick={() => setPicked(picked === c.key ? null : c.key)} />
                    ))}
                  </ul>}
            </div>
          )}

          {check && (
            <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <label style={{ flex: 1, minWidth: 240 }}>
                <span style={fieldLabel}>Note for the developers (optional)</span>
                <input value={note} onChange={(e) => setNote(e.target.value)} style={input}
                  placeholder={picked ? 'e.g. Still happens on 2.4.1' : 'e.g. Only on staging so far'} />
              </label>
              {picked && <button onClick={() => setPicked(null)} style={quietButton}>File as new instead</button>}
              <button onClick={file} disabled={!!busy} style={primaryButton(!!busy)}>
                {busy === 'file' ? 'Filing…' : picked ? `Add to ${picked}` : `File new ${TRACKER_LABEL[tracker!]} issue`}
              </button>
            </div>
          )}

          {preview && (
            <div style={{ marginTop: 12 }}>
              <button onClick={() => setShowPreview(!showPreview)} aria-expanded={showPreview}
                style={{ ...quietButton, padding: '3px 9px', fontSize: 11.5 }}>
                {showPreview ? 'Hide preview' : 'Preview what will be filed'}
              </button>
              {showPreview && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{preview.title}</div>
                  <pre aria-label="Issue preview" style={{
                    margin: 0, whiteSpace: 'pre-wrap', fontSize: 12, lineHeight: 1.55, color: 'var(--text-secondary)',
                    background: 'var(--panel-alt)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px',
                    maxHeight: 320, overflowY: 'auto', fontFamily: 'var(--font-mono)',
                  }}>{preview.markdown}</pre>
                  <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 4 }}>
                    {preview.attachments.length > 0 && `Attaches: ${preview.attachments.join(', ')}. `}
                    {preview.attachments_note}
                    {tracker === 'jira' && ' Jira gets the same content as formatted text.'}
                    {' '}To change it, edit the report above.
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
      <ErrorLine message={error} />
    </section>
  )
}
