import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import { useProject, useProjectPath } from '../project'
import RiskMatrix, { scoreBand } from '../components/RiskMatrix'
import ArtifactImport from '../components/ArtifactImport'
import { RiskHistory } from '../components/RiskUpdates'
import RiskForm, { ScoreSelect } from '../components/RiskForm'
import StoryChips from '../components/StoryChips'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from '../components/ui'
import {
  AI_PROVIDERS, api, Artifact, ChartersFromRisks, ModelElement, Project, ProjectChanges, ProductModel,
  Risk, RiskCharterDraft,
} from '../api/client'

const EVIDENCE_STYLE: Record<Risk['evidence'], { label: string; color: string; hint: string }> = {
  stated: { label: 'Stated', color: 'var(--info)', hint: 'The product model says this directly' },
  inferred: { label: 'Inferred', color: 'var(--text-secondary)', hint: 'Follows from the product model' },
  speculative: { label: 'Speculative', color: 'var(--warning)', hint: 'A plausible guess; check it before relying on it' },
  tester: { label: 'Tester', color: 'var(--question)', hint: 'Added by a tester' },
}

const CATEGORY_LABEL: Record<ModelElement['category'], string> = {
  structure: 'Structure', function: 'Function', data: 'Data', interfaces: 'Interfaces',
  platform: 'Platform', operations: 'Operations', time: 'Time',
}

function Section({ step, title, intro, children }: { step: number; title: string; intro: string; children: React.ReactNode }) {
  return (
    <section style={{ borderTop: '1px solid var(--border-soft)', padding: '26px 0' }} aria-labelledby={`step-${step}`}>
      <div style={{ display: 'flex', gap: 14, alignItems: 'baseline', marginBottom: 16 }}>
        <span className="mono" style={{ fontSize: 13, color: 'var(--accent)', fontWeight: 600 }}>{step}</span>
        <div>
          <h2 id={`step-${step}`} style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>{title}</h2>
          <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 640, lineHeight: 1.5 }}>{intro}</p>
        </div>
      </div>
      <div style={{ paddingLeft: 27 }}>{children}</div>
    </section>
  )
}

// ---------- page ----------

export default function RisksPage() {
  const { project, setProject } = useProject()
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [model, setModel] = useState<ProductModel | null>(null)
  const [risks, setRisks] = useState<Risk[]>([])
  const [retired, setRetired] = useState<Risk[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const refreshRisks = useCallback(async (projectId: string) => {
    const [active, gone] = await Promise.all([api.listRisks(projectId), api.listRisks(projectId, 'retired')])
    setRisks(active)
    setRetired(gone)
  }, [])

  useEffect(() => {
    async function load() {
      const [a, m] = await Promise.all([api.listArtifacts(project.id), api.getProductModel(project.id)])
      setArtifacts(a)
      setModel(m)
      await refreshRisks(project.id)
    }
    setLoading(true)
    load().catch((e) => setLoadError(errorText(e))).finally(() => setLoading(false))
  }, [project.id, refreshRisks])

  function replaceRisk(updated: Risk) {
    refreshRisks(project.id).catch(console.error)
    setRisks((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
  }

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1180, minWidth: 0 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Risks</h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '6px 0 0', maxWidth: 640, lineHeight: 1.5 }}>
          Decide what is worth testing first. The AI proposes a model of the project and risks to explore;
          you decide which risks are real, how serious they are, and which ones become charters.
        </p>

        {loading && <div style={{ color: 'var(--text-secondary)', marginTop: 24 }}>Loading…</div>}
        <ErrorLine message={loadError} />

        {!loading && (
          <>
            <MissionSettings project={project} onSaved={setProject} />
            <Section
              step={1}
              title="Model the project"
              intro="Pick the specs, diffs or bug histories that describe what changed. The AI maps them to the product elements of the Heuristic Test Strategy Model and lists open questions for your team."
            >
              <ArtifactsAndModel
                project={project}
                artifacts={artifacts}
                model={model}
                onArtifactAdded={(a) => setArtifacts((prev) => [...prev, a])}
                onModelBuilt={setModel}
              />
            </Section>
            <Section
              step={2}
              title="Review the risks"
              intro="AI-proposed risks stay in review until you accept them. Re-score anything that doesn't match what you know, retire what doesn't apply, and add risks the AI missed."
            >
              <RiskReview
                project={project}
                model={model}
                risks={risks}
                retired={retired}
                onAnalyzed={() => refreshRisks(project.id)}
                onChanged={replaceRisk}
                onAdded={() => refreshRisks(project.id)}
              />
            </Section>
            <Section
              step={3}
              title="Plan charters"
              intro="Turn accepted risks into exploratory charters. Each draft says which risks it investigates, with starting ideas and the oracles to watch; save the ones you want to run."
            >
              <CharterPlanner project={project} accepted={risks.filter((r) => r.status === 'accepted')} />
            </Section>
          </>
        )}
      </main>
    </div>
  )
}

// ---------- mission and AI provider (also on the Settings page) ----------

function MissionSettings({ project, onSaved }: { project: Project; onSaved: (p: Project) => void }) {
  const [mission, setMission] = useState(project.testing_mission ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dirty = mission.trim() !== (project.testing_mission ?? '')

  async function save(changes: ProjectChanges) {
    setSaving(true)
    setError(null)
    try {
      onSaved(await api.updateProject(project.id, changes))
    } catch (e) {
      setError(errorText(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ display: 'flex', gap: 20, alignItems: 'flex-end', flexWrap: 'wrap', margin: '24px 0 26px' }}>
      <div style={{ flex: '1 1 420px' }}>
        <label htmlFor="mission" style={fieldLabel}>Testing mission for {project.name}</label>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            id="mission"
            value={mission}
            onChange={(e) => setMission(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && dirty) save({ testing_mission: mission.trim() || null }) }}
            placeholder="e.g. Find release blockers in the new document upload flow"
            style={input}
          />
          <button onClick={() => save({ testing_mission: mission.trim() || null })} disabled={!dirty || saving} style={primaryButton(!dirty || saving)}>
            Save
          </button>
        </div>
      </div>
      <div>
        <label htmlFor="provider" style={fieldLabel}>AI provider</label>
        <select
          id="provider"
          value={project.default_ai_provider}
          onChange={(e) => save({ default_ai_provider: e.target.value })}
          disabled={saving}
          style={{ ...input, width: 140 }}
        >
          {AI_PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </div>
      <div style={{ flexBasis: '100%' }}><ErrorLine message={error} /></div>
    </div>
  )
}

// ---------- step 1: artifacts and product model ----------

function ArtifactsAndModel({ project, artifacts, model, onArtifactAdded, onModelBuilt }: {
  project: Project
  artifacts: Artifact[]
  model: ProductModel | null
  onArtifactAdded: (a: Artifact) => void
  onModelBuilt: (m: ProductModel) => void
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set(model?.artifact_ids ?? []))
  const [adding, setAdding] = useState(artifacts.length === 0)
  const [draft, setDraft] = useState({ type: 'spec' as Artifact['type'], label: '', raw_content: '' })
  const [mode, setMode] = useState<'paste' | 'import'>('paste')
  const [busy, setBusy] = useState<'add' | 'build' | null>(null)
  const [error, setError] = useState<string | null>(null)

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function addArtifact() {
    setBusy('add')
    setError(null)
    try {
      const a = await api.createArtifact(project.id, { ...draft, label: draft.label.trim() })
      onArtifactAdded(a)
      setSelected((prev) => new Set(prev).add(a.id))
      setDraft({ type: 'spec', label: '', raw_content: '' })
      setAdding(false)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(null)
    }
  }

  function imported(a: Artifact) {
    onArtifactAdded(a)
    setSelected((prev) => new Set(prev).add(a.id))
    setAdding(false)
  }

  /** A text file goes into the paste box; its name becomes the artifact's name. */
  async function loadFile(file: File) {
    const text = await file.text()
    const type: Artifact['type'] = /\.(diff|patch)$/i.test(file.name) ? 'diff' : draft.type
    setDraft({ type, label: draft.label || file.name, raw_content: text })
  }

  async function build() {
    setBusy('build')
    setError(null)
    try {
      onModelBuilt(await api.buildProductModel(project.id, [...selected]))
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(null)
    }
  }

  const canAdd = draft.label.trim().length > 0 && draft.raw_content.trim().length > 0
  const grouped = model
    ? (Object.keys(CATEGORY_LABEL) as ModelElement['category'][])
        .map((c) => [c, model.elements.filter((e) => e.category === c)] as const)
        .filter(([, items]) => items.length > 0)
    : []

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 380px) 1fr', gap: 28, alignItems: 'start' }}>
      <div>
        {artifacts.length > 0 && (
          <fieldset style={{ border: 'none', padding: 0, margin: 0 }}>
            <legend style={fieldLabel}>Artifacts to model</legend>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {artifacts.map((a) => (
                <label key={a.id} style={{
                  display: 'flex', gap: 10, alignItems: 'flex-start', padding: '9px 11px', borderRadius: 8, cursor: 'pointer',
                  border: `1px solid ${selected.has(a.id) ? 'var(--accent)' : 'var(--border)'}`,
                }}>
                  <input type="checkbox" checked={selected.has(a.id)} onChange={() => toggle(a.id)} style={{ marginTop: 2 }} />
                  <span style={{ minWidth: 0 }}>
                    <span style={{ fontSize: 13, display: 'block' }}>{a.label}</span>
                    <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>
                      {{ spec: 'Spec', diff: 'Diff', bug_import: 'Bug history', openapi: 'OpenAPI' }[a.type]}
                      {a.file_ref && (a.file_ref.startsWith('http')
                        ? <> · <a href={a.file_ref} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>imported ↗</a></>
                        : ' · imported')}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        )}

        {adding ? (
          <div style={{ marginTop: artifacts.length ? 14 : 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div role="tablist" aria-label="Add by" style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', alignSelf: 'flex-start' }}>
              {(['paste', 'import'] as const).map((m) => (
                <button key={m} role="tab" aria-selected={mode === m} onClick={() => setMode(m)} style={{
                  border: 'none', padding: '6px 14px', fontSize: 12.5, cursor: 'pointer', fontFamily: 'var(--font-ui)',
                  background: mode === m ? 'var(--accent-bg)' : 'transparent', color: mode === m ? 'var(--accent)' : 'var(--text-secondary)',
                }}>{m === 'paste' ? 'Paste' : 'Import'}</button>
              ))}
            </div>
            {mode === 'import' ? (
              <>
                <ArtifactImport project={project} onImported={imported} />
                {artifacts.length > 0 && <button onClick={() => setAdding(false)} style={{ ...quietButton, alignSelf: 'flex-start' }}>Cancel</button>}
              </>
            ) : (<>
            <div style={{ display: 'flex', gap: 8 }}>
              <div style={{ width: 130 }}>
                <label htmlFor="artifact-type" style={fieldLabel}>Type</label>
                <select id="artifact-type" value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value as Artifact['type'] })} style={input}>
                  <option value="spec">Spec</option>
                  <option value="diff">Diff</option>
                  <option value="bug_import">Bug history</option>
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label htmlFor="artifact-label" style={fieldLabel}>Name</label>
                <input id="artifact-label" value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })}
                  placeholder="e.g. upload-spec-v3.md" style={input} />
              </div>
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <label htmlFor="artifact-content" style={fieldLabel}>Content</label>
                <label style={{ fontSize: 11.5, color: 'var(--accent)', cursor: 'pointer' }}>
                  Load from file
                  <input type="file" accept=".md,.txt,.diff,.patch,.markdown,.csv,.json" aria-label="Load from file" style={{ display: 'none' }}
                    onChange={(e) => { const file = e.target.files?.[0]; if (file) loadFile(file); e.target.value = '' }} />
                </label>
              </div>
              <textarea id="artifact-content" value={draft.raw_content} onChange={(e) => setDraft({ ...draft, raw_content: e.target.value })}
                placeholder="Paste the spec text, git diff or bug list"
                style={{ ...input, height: 140, fontFamily: 'var(--font-mono)', fontSize: 12, resize: 'vertical' }} />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button onClick={addArtifact} disabled={!canAdd || busy !== null} style={primaryButton(!canAdd || busy !== null)}>
                {busy === 'add' ? 'Adding…' : 'Add artifact'}
              </button>
              {artifacts.length > 0 && <button onClick={() => setAdding(false)} style={quietButton}>Cancel</button>}
            </div>
            </>)}
          </div>
        ) : (
          <button onClick={() => setAdding(true)} style={{ ...quietButton, marginTop: 10 }}>Add an artifact</button>
        )}

        {artifacts.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <button onClick={build} disabled={selected.size === 0 || busy !== null} style={primaryButton(selected.size === 0 || busy !== null)}>
              {busy === 'build' ? 'Building model…' : model ? 'Rebuild product model' : 'Build product model'}
            </button>
          </div>
        )}
        <ErrorLine message={error} />
      </div>

      <div style={{ minWidth: 0 }}>
        {!model ? (
          <div style={{ fontSize: 13, color: 'var(--text-tertiary)', border: '1px dashed var(--border)', borderRadius: 10, padding: 18 }}>
            The product model will appear here: what the project is made of, what it does, and what nobody has written down yet.
          </div>
        ) : (
          <div>
            <p style={{ fontSize: 13.5, lineHeight: 1.6, margin: '0 0 16px', maxWidth: 680 }}>{model.summary}</p>
            {model.truncated_artifacts.length > 0 && (
              <p style={{ fontSize: 12, color: 'var(--warning)', margin: '0 0 12px' }}>
                Cut to fit: {model.truncated_artifacts.join(', ')}. Split long artifacts for a fuller model.
              </p>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {grouped.map(([category, items]) => (
                <div key={category} style={{ display: 'flex', gap: 12 }}>
                  <div style={{ width: 92, flexShrink: 0, fontSize: 12, color: 'var(--text-tertiary)', paddingTop: 4 }}>{CATEGORY_LABEL[category]}</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {items.map((e) => (
                      <span key={e.name} title={e.description} style={{
                        fontSize: 12, background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 6, padding: '4px 9px',
                      }}>{e.name}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            {model.unknowns.length > 0 && (
              <div style={{ marginTop: 18 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Questions for the team</div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  {model.unknowns.map((q) => <li key={q}>{q}</li>)}
                </ul>
              </div>
            )}
            <p style={{ fontSize: 11, color: 'var(--text-quiet)', marginTop: 14 }}>
              Drafted by {model.ai_provider}/{model.ai_model} with {model.prompt_version}, {new Date(model.created_at + 'Z').toLocaleString()}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------- step 2: risk review ----------

function RiskReview({ project, model, risks, retired, onAnalyzed, onChanged, onAdded }: {
  project: Project
  model: ProductModel | null
  risks: Risk[]
  retired: Risk[]
  onAnalyzed: () => Promise<void>
  onChanged: (r: Risk) => void
  onAdded: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showRetired, setShowRetired] = useState(false)
  const [addingRisk, setAddingRisk] = useState(false)
  const cards = useRef<Record<string, HTMLElement | null>>({})

  async function analyze() {
    setBusy(true)
    setError(null)
    try {
      await api.analyzeRisks(project.id)
      await onAnalyzed()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  function select(r: Risk) {
    setSelectedId(r.id)
    cards.current[r.id]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  const proposed = risks.filter((r) => r.status === 'proposed')
  const accepted = risks.filter((r) => r.status === 'accepted')

  return (
    <div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 18 }}>
        <button onClick={analyze} disabled={!model || busy} style={primaryButton(!model || busy)}>
          {busy ? 'Analysing…' : risks.length ? 'Find more risks' : 'Analyse risks'}
        </button>
        <button onClick={() => setAddingRisk((v) => !v)} style={quietButton}>{addingRisk ? 'Close' : 'Add a risk yourself'}</button>
        {!model && <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Build a product model first.</span>}
      </div>
      <ErrorLine message={error} />
      {addingRisk && (
        <RiskForm save={async (r) => { await api.createRisk(project.id, r); await onAdded(); setAddingRisk(false) }} />
      )}

      {risks.length === 0 && !addingRisk ? (
        <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>No risks yet.</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 28, alignItems: 'start' }}>
          <div style={{ position: 'sticky', top: 20 }}>
            <RiskMatrix risks={risks} selectedId={selectedId} onSelect={select} />
          </div>
          <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 22 }}>
            <RiskGroup title={`Needs review (${proposed.length})`} empty="Nothing waiting for review.">
              {proposed.map((r) => (
                <RiskCard key={r.id} risk={r} selected={r.id === selectedId} onChanged={onChanged}
                  refCallback={(el) => { cards.current[r.id] = el }} />
              ))}
            </RiskGroup>
            <RiskGroup title={`Accepted (${accepted.length})`} empty="Accept the risks you agree with; only accepted risks become charters.">
              {accepted.map((r) => (
                <RiskCard key={r.id} risk={r} selected={r.id === selectedId} onChanged={onChanged}
                  refCallback={(el) => { cards.current[r.id] = el }} />
              ))}
            </RiskGroup>
            {retired.length > 0 && (
              <div>
                <button onClick={() => setShowRetired((v) => !v)} style={{ ...quietButton, border: 'none', padding: 0 }}>
                  {showRetired ? 'Hide' : 'Show'} retired risks ({retired.length})
                </button>
                {showRetired && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
                    {retired.map((r) => <RiskCard key={r.id} risk={r} selected={false} onChanged={onChanged} />)}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function RiskGroup({ title, empty, children }: { title: string; empty: string; children: React.ReactNode[] }) {
  return (
    <div>
      <h3 style={{ fontSize: 13.5, fontWeight: 600, margin: '0 0 10px' }}>{title}</h3>
      {children.length === 0
        ? <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)' }}>{empty}</div>
        : <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>{children}</div>}
    </div>
  )
}

function RiskCard({ risk, selected, onChanged, refCallback }: {
  risk: Risk
  selected: boolean
  onChanged: (r: Risk) => void
  refCallback?: (el: HTMLElement | null) => void
}) {
  const [notes, setNotes] = useState(risk.tester_notes ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const band = scoreBand(risk.score)
  const evidence = EVIDENCE_STYLE[risk.evidence]

  async function update(changes: Parameters<typeof api.updateRisk>[1]) {
    setSaving(true)
    setError(null)
    try {
      onChanged(await api.updateRisk(risk.id, changes))
    } catch (e) {
      setError(errorText(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <article
      ref={refCallback}
      aria-label={`${risk.ref} ${risk.title}`}
      style={{
        border: `1px solid ${selected ? 'var(--text-secondary)' : 'var(--border)'}`, borderRadius: 10, padding: '13px 15px',
        background: risk.status === 'accepted' ? 'var(--panel)' : 'transparent',
        opacity: risk.status === 'retired' ? 0.65 : 1,
      }}
    >
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <span className="mono" style={{
          fontSize: 11, fontWeight: 600, padding: '3px 7px', borderRadius: 5, flexShrink: 0,
          color: band.fg, background: band.bg,
        }} title={`${band.label} priority`}>{risk.ref} · {risk.score}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{risk.title}</div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 3, lineHeight: 1.5 }}>{risk.failure_mode}</div>
          <StoryChips stories={risk.stories ?? []} />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap', marginTop: 11, paddingLeft: 2 }}>
        <ScoreSelect label="Impact" value={risk.impact} disabled={saving} onChange={(v) => update({ impact: v })} />
        <ScoreSelect label="Likelihood" value={risk.likelihood} disabled={saving} onChange={(v) => update({ likelihood: v })} />
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{risk.quality_criterion}</span>
        <span title={evidence.hint} style={{ fontSize: 12, color: evidence.color }}>{evidence.label}</span>
      </div>

      {risk.rationale && (
        <p style={{ fontSize: 12.5, color: 'var(--text-tertiary)', margin: '10px 0 0', lineHeight: 1.5 }}>{risk.rationale}</p>
      )}

      <div style={{ marginTop: 10 }}>
        <label htmlFor={`notes-${risk.id}`} style={{ ...fieldLabel, marginBottom: 4 }}>Your notes (steer the charters)</label>
        <textarea
          id={`notes-${risk.id}`}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() => { if (notes !== (risk.tester_notes ?? '')) update({ tester_notes: notes || null }) }}
          placeholder="e.g. Last release broke rounding here"
          rows={1}
          style={{ ...input, fontSize: 12.5, resize: 'vertical' }}
        />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, marginTop: 11, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, color: 'var(--text-quiet)' }}>
          {[
            risk.last_change,
            risk.source === 'ai' ? `Proposed by ${risk.ai_provider}/${risk.ai_model}` : risk.last_change ? null : 'Added by a tester',
          ].filter(Boolean).join('. ')}
        </span>
        <div style={{ display: 'flex', gap: 6 }}>
          {risk.status === 'proposed' && (
            <button onClick={() => update({ status: 'accepted' })} disabled={saving} style={{ ...primaryButton(saving), padding: '6px 13px' }}>Accept</button>
          )}
          {risk.status !== 'retired' && (
            <button onClick={() => update({ status: 'retired' })} disabled={saving} style={quietButton}>Retire</button>
          )}
          {risk.status === 'retired' && (
            <button onClick={() => update({ status: 'proposed' })} disabled={saving} style={quietButton}>Reopen</button>
          )}
        </div>
      </div>
      <RiskHistory riskId={risk.id} version={risk.updated_at} />
      <ErrorLine message={error} />
    </article>
  )
}

// ---------- step 3: charters from risks ----------

function CharterPlanner({ project, accepted }: { project: Project; accepted: Risk[] }) {
  const charterPath = useProjectPath()('/charters/suggest')
  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [result, setResult] = useState<ChartersFromRisks | null>(null)
  const [saved, setSaved] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const chosen = accepted.filter((r) => !excluded.has(r.id))

  function toggle(id: string) {
    setExcluded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function draft() {
    setBusy(true)
    setError(null)
    try {
      setResult(await api.chartersFromRisks(project.id, chosen.map((r) => r.id)))
      setSaved(new Set())
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function save(d: RiskCharterDraft, index: number) {
    setError(null)
    try {
      await api.saveCharter(project.id, {
        mission_statement: d.mission_statement, rationale: d.rationale, priority: d.priority,
        source: 'risk-based', risk_ids: d.risk_ids, test_ideas: d.test_ideas, oracles: d.oracles,
        risk_areas: accepted.filter((r) => d.risk_ids.includes(r.id)).map((r) => r.title),
      })
      setSaved((prev) => new Set(prev).add(index))
    } catch (e) {
      setError(errorText(e))
    }
  }

  if (accepted.length === 0) {
    return <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>Accept at least one risk to plan charters from it.</div>
  }

  return (
    <div>
      <fieldset style={{ border: 'none', padding: 0, margin: '0 0 14px' }}>
        <legend style={fieldLabel}>Plan charters for</legend>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {accepted.map((r) => (
            <label key={r.id} style={{
              display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, padding: '6px 10px', borderRadius: 7, cursor: 'pointer',
              border: `1px solid ${excluded.has(r.id) ? 'var(--border)' : 'var(--accent)'}`,
            }}>
              <input type="checkbox" checked={!excluded.has(r.id)} onChange={() => toggle(r.id)} />
              <span className="mono" style={{ fontSize: 11 }}>{r.ref}</span> {r.title}
            </label>
          ))}
        </div>
      </fieldset>
      <button onClick={draft} disabled={chosen.length === 0 || busy} style={primaryButton(chosen.length === 0 || busy)}>
        {busy ? 'Drafting…' : 'Draft charters'}
      </button>
      <ErrorLine message={error} />

      {result && (
        <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 820 }}>
          {result.warnings.map((w) => (
            <div key={w} style={{ fontSize: 12, color: 'var(--warning)' }}>{w}</div>
          ))}
          {result.uncovered_risk_refs.length > 0 && (
            <div style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
              No charter covers {result.uncovered_risk_refs.join(', ')} yet. Draft again with just those selected, or write one yourself.
            </div>
          )}
          {result.charters.map((d, i) => (
            <article key={i} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '15px 17px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14 }}>
                <div style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.45 }}>{d.mission_statement}</div>
                {saved.has(i) ? (
                  <Link to={charterPath} style={{ fontSize: 12.5, whiteSpace: 'nowrap' }}>Saved. Start it from Charters</Link>
                ) : (
                  <button onClick={() => save(d, i)} style={{ ...primaryButton(), padding: '6px 13px', whiteSpace: 'nowrap', height: 'fit-content' }}>
                    Save charter
                  </button>
                )}
              </div>
              <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', margin: '6px 0 0', lineHeight: 1.5 }}>{d.rationale}</p>
              <div style={{ display: 'flex', gap: 24, marginTop: 12, flexWrap: 'wrap' }}>
                <div style={{ flex: '1 1 320px' }}>
                  <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginBottom: 4 }}>Starting ideas</div>
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.6 }}>
                    {d.test_ideas.map((idea) => <li key={idea}>{idea}</li>)}
                  </ul>
                </div>
                <div>
                  <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginBottom: 4 }}>Covers</div>
                  <div className="mono" style={{ fontSize: 12 }}>{d.risk_refs.join(', ')}</div>
                  <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', margin: '10px 0 4px' }}>Oracles</div>
                  <div style={{ fontSize: 12.5 }}>{d.oracles.map((o) => o.replace(/_/g, ' ')).join(', ')}</div>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  )
}
