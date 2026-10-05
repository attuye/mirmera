/** The project's test strategy (or a sprint's), in Heuristic Test Strategy Model sections:
 *  drafted from what Mirmera knows, optionally written as prose by AI, edited by the tester,
 *  saved as versions, exported. "What we won't test" is the team's call, never Mirmera's. */
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Download, Printer } from 'lucide-react'
import Sidebar from '../components/Sidebar'
import { utcDate } from '../components/Evidence'
import { ErrorLine, errorText, input, primaryButton, quietButton } from '../components/ui'
import { api, SprintSummary, StrategyOverview, StrategyVersion, strategyExportUrl } from '../api/client'
import { useProject } from '../project'

type Provenance = { ai_provider: string; ai_model: string | null; prompt_version: string } | null
const provenance = (d: Provenance) => (d ? { ai_provider: d.ai_provider, ai_model: d.ai_model, prompt_version: d.prompt_version } : null)
const small: React.CSSProperties = { ...quietButton, fontSize: 12, padding: '5px 10px' }

export default function StrategyPage() {
  const { project } = useProject()
  const [params, setParams] = useSearchParams()
  const sprintId = params.get('sprint') ?? ''
  const [sprints, setSprints] = useState<SprintSummary[]>([])
  const [data, setData] = useState<StrategyOverview | null>(null)
  const [viewing, setViewing] = useState<StrategyVersion | null>(null)  // the saved version in the editor, if any
  const [text, setText] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState<Record<string, string>>({})  // what the editor started from
  const [ai, setAi] = useState<Provenance>(null)
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { api.listSprints(project.id).then(setSprints).catch(() => {}) }, [project.id])
  useEffect(() => {
    setData(null); setError(null); setStatus(null)
    api.strategy(project.id, sprintId || undefined).then((d) => {
      setData(d)
      const latest = d.versions[0] ?? null
      setViewing(latest); setAi(null)
      const start = latest ? latest.sections : d.draft
      setText(start); setSaved(start)
    }).catch((e) => setError(errorText(e)))
  }, [project.id, sprintId])

  const dirty = JSON.stringify(text) !== JSON.stringify(saved)
  const next = (data?.versions[0]?.version ?? 0) + 1
  const sprint = sprints.find((s) => s.id === sprintId)

  function replaceAll(sections: Record<string, string>, provenance: Provenance, message: string) {
    if (dirty && !window.confirm('Replace what you have written with a new draft?')) return
    setText({ ...text, ...sections }); setAi(provenance); setViewing(null); setStatus(message)
  }
  async function save() {
    setBusy('save'); setError(null); setStatus(null)
    try {
      // Only the provenance: the AI's own sections must not overwrite what the tester edited.
      const v = await api.saveStrategy(project.id, { sprint_id: sprintId || null, sections: text, ...provenance(ai) })
      setData((d) => d && { ...d, versions: [v, ...d.versions] })
      setViewing(v); setSaved(v.sections); setText(v.sections); setAi(null)
      setStatus(`Saved as version ${v.version}.`)
    } catch (e) { setError(errorText(e)) } finally { setBusy(null) }
  }
  async function aiDraft() {
    setBusy('ai'); setError(null); setStatus(null)
    try {
      const d = await api.strategyAiDraft(project.id, sprintId || undefined)
      replaceAll(d.sections, provenance(d), 'Drafted by AI from the facts below. Read it, change what is wrong, then save.')
    } catch (e) { setError(errorText(e)) } finally { setBusy(null) }
  }

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px 60px', maxWidth: 1000 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Test strategy</h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 740, lineHeight: 1.55 }}>
          What you'll test, how and why, and what you won't: a living document to argue with and revise as you learn, not a
          test plan. Start from what Mirmera knows about {project.name}, rewrite it in your own words, and save a version
          when it says what you mean.
        </p>

        <div role="group" aria-label="Strategy" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 18 }}>
          <label style={{ fontSize: 12.5, color: 'var(--text-secondary)', display: 'flex', gap: 6, alignItems: 'center' }}>For
            <select aria-label="Focus" value={sprintId} onChange={(e) => setParams(e.target.value ? { sprint: e.target.value } : {})}
              style={{ ...input, width: 'auto', fontSize: 12.5, padding: '5px 8px' }}>
              <option value="">The whole project</option>
              {sprints.map((s) => <option key={s.id} value={s.id}>{s.name}{s.state === 'current' ? ' (current)' : ''}</option>)}
            </select>
          </label>
          {data && data.versions.length > 0 && (
            <select aria-label="Version" value={viewing?.id ?? ''} style={{ ...input, width: 'auto', fontSize: 12.5, padding: '5px 8px' }}
              onChange={(e) => {
                const v = data.versions.find((x) => x.id === e.target.value)
                if (!v || (dirty && !window.confirm('Open this version? What you have changed will be lost.'))) return
                setViewing(v); setText(v.sections); setSaved(v.sections); setAi(null); setStatus(null)
              }}>
              {!viewing && <option value="">Not saved yet</option>}
              {data.versions.map((v, i) => (
                <option key={v.id} value={v.id}>Version {v.version}{i === 0 ? ' (latest)' : ''} · {utcDate(v.created_at).toLocaleDateString()}{v.author ? ` · ${v.author}` : ''}</option>
              ))}
            </select>
          )}
          <button disabled={!data || !!busy} onClick={() => data && replaceAll(data.draft, null, 'Drafted from what Mirmera knows. Rewrite it in your words, then save.')} style={small}>
            Draft from the project
          </button>
          <button disabled={!data || !!busy} onClick={aiDraft} style={small}>{busy === 'ai' ? 'Writing…' : 'Write it as prose with AI'}</button>
          {viewing && !dirty && (
            <span style={{ display: 'inline-flex', gap: 6, marginLeft: 'auto' }}>
              <a href={strategyExportUrl(project.id, viewing.id, 'md')} download style={{ ...small, display: 'inline-flex', gap: 5, alignItems: 'center' }}><Download size={13} aria-hidden />Markdown</a>
              <a href={strategyExportUrl(project.id, viewing.id, 'html')} target="_blank" rel="noreferrer" style={{ ...small, display: 'inline-flex', gap: 5, alignItems: 'center' }}><Printer size={13} aria-hidden />Print</a>
            </span>
          )}
        </div>
        {sprint?.goal && <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 8 }}>{sprint.name} goal: {sprint.goal}</div>}
        {ai && <div role="note" style={{ fontSize: 12, color: 'var(--question)', marginTop: 10 }}>Drafted by AI ({ai.ai_provider}{ai.ai_model ? ` · ${ai.ai_model}` : ''}): it only used the facts under each section, but read it before saving.</div>}
        {status && <div role="status" style={{ fontSize: 12.5, color: 'var(--accent)', marginTop: 10 }}>{status}</div>}
        <ErrorLine message={error} />

        {data && data.sections.map((s) => {
          const facts = data.facts[s.key] ?? []
          const rows = Math.min(14, Math.max(3, (text[s.key] ?? '').split('\n').length + 1))
          return (
            <section key={s.key} aria-label={s.title} style={{ marginTop: 22 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
                <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>{s.title}</h2>
                <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{s.hint}</span>
              </div>
              <textarea aria-label={s.title} value={text[s.key] ?? ''} rows={rows}
                onChange={(e) => setText({ ...text, [s.key]: e.target.value })}
                style={{ ...input, marginTop: 8, fontSize: 13, lineHeight: 1.55, resize: 'vertical' }} />
              {facts.length > 0 && (
                <>
                  <button type="button" aria-expanded={open.has(s.key)} onClick={() => setOpen((o) => { const n = new Set(o); n.has(s.key) ? n.delete(s.key) : n.add(s.key); return n })}
                    style={{ background: 'none', border: 'none', padding: '4px 0', color: 'var(--text-tertiary)', fontSize: 11.5, cursor: 'pointer' }}>
                    {open.has(s.key) ? '▾' : '▸'} What Mirmera knows ({facts.length})
                  </button>
                  {open.has(s.key) && (
                    <ul aria-label={`What Mirmera knows: ${s.title}`} style={{ margin: '2px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                      {facts.map((f) => <li key={f}>{f}</li>)}
                    </ul>
                  )}
                </>
              )}
            </section>
          )
        })}

        {data && (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 26, position: 'sticky', bottom: 0, background: 'var(--bg)', padding: '12px 0' }}>
            <button onClick={save} disabled={!!busy || (!dirty && !!viewing)} style={primaryButton(!!busy || (!dirty && !!viewing))}>
              {busy === 'save' ? 'Saving…' : `Save as version ${next}`}
            </button>
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
              {dirty ? 'Changed since ' + (viewing ? `version ${viewing.version}` : 'the draft') : viewing ? `Showing version ${viewing.version}` : 'A draft, not saved yet'}
              {sprint ? ` · for ${sprint.name}` : ' · for the whole project'}
            </span>
          </div>
        )}
      </main>
    </div>
  )
}
