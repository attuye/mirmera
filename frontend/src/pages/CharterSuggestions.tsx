/** Charters: the project's charters (start or resume a session from one), and
 *  quick suggestions from a change: a pasted diff or spec, an imported pull
 *  request or local diff, or something already imported. The slower, whole-
 *  product path (model, risks, charters) lives on the Risks page. */
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import TesterField, { useTesterName } from '../components/TesterField'
import ArtifactImport from '../components/ArtifactImport'
import HeuristicChips from '../components/HeuristicChips'
import StoryChips from '../components/StoryChips'
import { ErrorLine, errorText, fieldLabel, input, primaryButton } from '../components/ui'
import { API_URL, api, Artifact, Charter, CharterSuggestion, StoryDetail } from '../api/client'
import { useProject, useProjectPath } from '../project'

const PRIORITY_COLOR: Record<string, string> = { high: 'var(--danger)', medium: 'var(--warning)', low: 'var(--text-tertiary)' }
const KIND: Record<Artifact['type'], string> = { spec: 'Spec', diff: 'Diff', bug_import: 'Bug history' }
type Mode = 'paste' | 'import' | 'earlier'

function SourceTag({ label, url, prefix = 'from' }: { label: string | null; url: string | null; prefix?: string }) {
  if (!label) return null
  const text = prefix ? `${prefix} ${label}` : label
  return url
    ? <a href={url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} style={{ fontSize: 11.5, color: 'var(--info)' }}>{text} ↗</a>
    : <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{text}</span>
}

/** A name for pasted text: its first meaningful line, so the artifact list stays readable. */
function nameFor(text: string): string {
  const pr = text.match(/^diff --git a\/(\S+)/m)
  const first = text.split('\n').map((l) => l.trim()).find((l) => l && !l.startsWith('---') && !l.startsWith('+++'))
  const base = pr ? `diff of ${pr[1]}` : (first ?? 'pasted change')
  return `Pasted: ${base.length > 60 ? `${base.slice(0, 59)}…` : base}`
}

export default function CharterSuggestions() {
  const { project } = useProject()
  const path = useProjectPath()
  const navigate = useNavigate()
  const [tester, setTester] = useTesterName(project)
  const [charters, setCharters] = useState<Charter[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [mode, setMode] = useState<Mode>('paste')
  const [pasted, setPasted] = useState({ name: '', text: '' })
  const [earlier, setEarlier] = useState('')
  const [source, setSource] = useState<Artifact | null>(null)  // what the suggestions came from
  const [suggestions, setSuggestions] = useState<CharterSuggestion[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [startError, setStartError] = useState<string | null>(null)
  const [params] = useSearchParams()
  const storyId = params.get('story')
  const [story, setStory] = useState<StoryDetail | null>(null)  // suggestions for this story are linked to it

  useEffect(() => {
    api.listCharters(project.id).then(setCharters).catch(console.error)
    api.listArtifacts(project.id).then((a) => { setArtifacts(a); setEarlier(a[a.length - 1]?.id ?? '') }).catch(console.error)
  }, [project.id])
  useEffect(() => {  // from a story's page: suggest charters for the story straight away
    if (!storyId) { setStory(null); return }
    api.getStory(storyId).then(async (s) => { const a = await api.storyArtifact(s.id); setStory({ ...s, artifact_id: a.id }); await suggestFrom(a) })
      .catch((e) => setError(errorText(e)))
  }, [storyId])  // eslint-disable-line react-hooks/exhaustive-deps

  async function suggestFrom(artifact: Artifact) {
    setBusy(true); setError(null); setSuggestions([]); setSource(artifact)
    setArtifacts((prev) => prev.some((a) => a.id === artifact.id) ? prev : [...prev, artifact])
    try {
      setSuggestions(await api.suggestCharters(project.id, [artifact.id]))
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setError(message === 'Failed to fetch' ? `Could not reach the backend. Is it running on ${API_URL}?` : `Could not suggest charters: ${message}`)
    } finally { setBusy(false) }
  }

  async function fromPaste() {
    if (!pasted.text.trim()) return
    setBusy(true); setError(null)
    try {
      const type: Artifact['type'] = /^(diff --git|@@ |--- a\/)/m.test(pasted.text) ? 'diff' : 'spec'
      const artifact = await api.createArtifact(project.id, { type, label: pasted.name.trim() || nameFor(pasted.text), raw_content: pasted.text })
      setPasted({ name: '', text: '' })
      await suggestFrom(artifact)
    } catch (e) { setError(errorText(e)); setBusy(false) }
  }

  async function loadFile(file: File) {
    setPasted({ name: pasted.name || file.name, text: await file.text() })
  }

  async function accept(s: CharterSuggestion, index: number) {
    try {
      const charter = await api.saveCharter(project.id, {
        mission_statement: s.mission_statement, rationale: s.rationale, risk_areas: s.risk_areas, priority: s.priority,
        source: 'ai-suggested', source_artifact_id: source?.id ?? null,
        story_ids: story && source?.id === story.artifact_id ? [story.id] : [],
      })
      setCharters((prev) => [...prev, charter])
      setSuggestions((prev) => prev.filter((_, i) => i !== index))
    } catch (e) { setError(errorText(e)) }
  }

  async function open(c: Charter) {
    setStartError(null)
    try {
      if (c.status === 'active') {
        const session = await api.getActiveSession(c.id)
        navigate(path(`/sessions/${session.id}`))
      } else if (c.status === 'proposed') {
        if (!tester.trim()) { setStartError('Enter your name under "Testing as" first.'); return }
        const session = await api.startSession(c.id, tester.trim())
        navigate(path(`/sessions/${session.id}`))
      }
    } catch (e) { setStartError(errorText(e)) }
  }

  const tab = (m: Mode, label: string) => (
    <button key={m} role="tab" aria-selected={mode === m} onClick={() => setMode(m)} style={{
      border: 'none', padding: '6px 14px', fontSize: 12.5, cursor: 'pointer', fontFamily: 'var(--font-ui)',
      background: mode === m ? 'var(--accent-bg)' : 'transparent', color: mode === m ? 'var(--accent)' : 'var(--text-secondary)',
    }}>{label}</button>
  )

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1100 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Charters</h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 680, lineHeight: 1.5 }}>
          Missions for exploratory sessions. Start one here, or get quick suggestions from a change below. For charters
          from the whole product's risks, use the <Link to={path('/risks')}>Risks</Link> page.
        </p>

        {story && (
          <div role="note" aria-label="For a story" style={{
            marginTop: 16, maxWidth: 760, padding: '10px 14px', borderRadius: 9, background: 'var(--accent-bg)', fontSize: 13,
            display: 'flex', gap: 12, alignItems: 'center',
          }}>
            <span style={{ flex: 1 }}>Suggesting charters for <b>{story.key ? `${story.key}: ` : ''}{story.title}</b>. Accepted ones are linked to the story.</span>
            <Link to={path(`/stories/${story.id}`)} style={{ whiteSpace: 'nowrap', fontWeight: 600 }}>Back to the story →</Link>
          </div>
        )}

        <section aria-label="Your charters" style={{ marginTop: 22 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', maxWidth: 760, marginBottom: 10 }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Your charters <span style={{ fontWeight: 400, color: 'var(--text-tertiary)', fontSize: 13 }}>{charters.length}</span></h2>
            <TesterField value={tester} onChange={setTester} />
          </div>
          {startError && <div role="alert" style={{ color: 'var(--danger)', fontSize: 12.5, marginBottom: 8 }}>{startError}</div>}
          {charters.length === 0 && <p style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>None yet: suggest some from a change below, or from risks.</p>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 760 }}>
            {charters.map((c) => (
              <div key={c.id} role="listitem" aria-label={c.mission_statement} onClick={() => open(c)} style={{
                border: '1px solid var(--border)', borderRadius: 9, padding: '10px 14px', display: 'flex', justifyContent: 'space-between',
                alignItems: 'center', gap: 12, cursor: c.status === 'active' || c.status === 'proposed' ? 'pointer' : 'default',
              }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13 }}>{c.mission_statement}</div>
                  <SourceTag label={c.source_label} url={c.source_url} />
                  <StoryChips stories={c.stories} />
                  <HeuristicChips heuristics={c.heuristics} oracles={c.oracles} />
                </div>
                <span className="mono" style={{ fontSize: 9.5, color: c.status === 'active' ? 'var(--accent)' : 'var(--text-tertiary)', flexShrink: 0 }}>
                  {c.status.toUpperCase()}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section aria-label="Suggest from a change" style={{ marginTop: 30 }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 4px' }}>Suggest from a change</h2>
          <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', margin: '0 0 12px' }}>
            A pull request, a diff or a spec excerpt: the AI suggests charters for what it changes.
          </p>
          <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
            <div style={{ width: 440, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div role="tablist" aria-label="Suggest from" style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', alignSelf: 'flex-start' }}>
                {tab('paste', 'Paste')}{tab('import', 'Import')}{artifacts.length > 0 && tab('earlier', 'Imported before')}
              </div>
              {mode === 'paste' && (
                <>
                  <div>
                    <label htmlFor="change-name" style={fieldLabel}>Name (optional)</label>
                    <input id="change-name" value={pasted.name} onChange={(e) => setPasted({ ...pasted, name: e.target.value })} style={input}
                      placeholder="e.g. Passport MRZ check" />
                  </div>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                      <label htmlFor="change-text" style={fieldLabel}>Diff, spec excerpt or bug summary</label>
                      <label style={{ fontSize: 11.5, color: 'var(--accent)', cursor: 'pointer' }}>
                        Load from file
                        <input type="file" accept=".md,.txt,.diff,.patch,.markdown" aria-label="Load from file" style={{ display: 'none' }}
                          onChange={(e) => { const f = e.target.files?.[0]; if (f) loadFile(f); e.target.value = '' }} />
                      </label>
                    </div>
                    <textarea id="change-text" value={pasted.text} onChange={(e) => setPasted({ ...pasted, text: e.target.value })}
                      placeholder="Paste a git diff or spec text here…"
                      style={{ ...input, height: 200, fontFamily: 'var(--font-mono)', fontSize: 12, resize: 'vertical' }} />
                  </div>
                  <div><button onClick={fromPaste} disabled={busy || !pasted.text.trim()} style={primaryButton(busy || !pasted.text.trim())}>
                    {busy ? 'Suggesting…' : 'Suggest charters'}
                  </button></div>
                </>
              )}
              {mode === 'import' && <ArtifactImport project={project} onImported={suggestFrom} actionLabel="Import and suggest" busyLabel="Importing…" />}
              {mode === 'earlier' && (
                <>
                  <div>
                    <label htmlFor="earlier-artifact" style={fieldLabel}>Artifact</label>
                    <select id="earlier-artifact" value={earlier} onChange={(e) => setEarlier(e.target.value)} style={input}>
                      {[...artifacts].reverse().map((a) => <option key={a.id} value={a.id}>{KIND[a.type]}: {a.label}</option>)}
                    </select>
                  </div>
                  <div><button onClick={() => { const a = artifacts.find((x) => x.id === earlier); if (a) suggestFrom(a) }} disabled={busy || !earlier}
                    style={primaryButton(busy || !earlier)}>{busy ? 'Suggesting…' : 'Suggest charters'}</button></div>
                </>
              )}
              <ErrorLine message={error} />
            </div>

            <div aria-label="Suggestions" role="region" style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
              {busy && <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>Asking the AI…</div>}
              {!busy && suggestions.length === 0 && (
                <div style={{ color: 'var(--text-tertiary)', fontSize: 13 }}>{source ? 'All suggestions handled.' : 'Suggestions will appear here.'}</div>
              )}
              {source && suggestions.length > 0 && (
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                  Suggestions for <SourceTag label={source.label} url={source.file_ref?.startsWith('http') ? source.file_ref : null} prefix="" />
                </div>
              )}
              {suggestions.map((s, i) => (
                <article key={i} aria-label={s.mission_statement} style={{ border: '1px solid var(--border)', borderRadius: 11, padding: '16px 18px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
                    <div style={{ fontSize: 14, fontWeight: 600 }}>{s.mission_statement}</div>
                    <span className="mono" style={{ fontSize: 10, color: PRIORITY_COLOR[s.priority], flexShrink: 0 }}>{s.priority.toUpperCase()}</span>
                  </div>
                  <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 8, lineHeight: 1.5 }}>{s.rationale}</div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, gap: 12 }}>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {s.risk_areas.map((r) => (
                        <span key={r} className="mono" style={{ fontSize: 9.5, color: 'var(--danger)', background: 'var(--danger-bg)', padding: '3px 7px', borderRadius: 5 }}>{r}</span>
                      ))}
                    </div>
                    <button onClick={() => accept(s, i)} style={{ fontSize: 12, fontWeight: 600, color: '#0C0D0F', background: 'var(--accent)', padding: '5px 14px', borderRadius: 6 }}>
                      Accept
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}
