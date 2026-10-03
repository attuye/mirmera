/** A project's settings, saved together. */
import { useState } from 'react'
import Sidebar from '../components/Sidebar'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from '../components/ui'
import { AI_PROVIDERS, api, Environment, Project, ProjectChanges } from '../api/client'
import { useProject } from '../project'

interface Form {
  name: string
  description: string
  app_url: string
  environments: Environment[]
  testing_mission: string
  default_ai_provider: string
  default_tester: string
  github_repo: string
  jira_project_key: string
}

const toForm = (p: Project): Form => ({
  name: p.name, description: p.description ?? '', app_url: p.app_url ?? '',
  environments: p.environments.map((e) => ({ ...e })), testing_mission: p.testing_mission ?? '',
  default_ai_provider: p.default_ai_provider, default_tester: p.default_tester ?? '',
  github_repo: p.github_repo ?? '', jira_project_key: p.jira_project_key ?? '',
})

/** Only what changed, trimmed; "" clears a field on the server. */
function changes(form: Form, saved: Form): ProjectChanges {
  const out: ProjectChanges = {}
  for (const key of ['name', 'description', 'app_url', 'testing_mission', 'default_ai_provider', 'default_tester', 'github_repo', 'jira_project_key'] as const) {
    if (form[key].trim() !== saved[key]) (out as Record<string, string>)[key] = form[key].trim()
  }
  const envs = form.environments.map((e) => ({ name: e.name.trim(), url: e.url.trim() })).filter((e) => e.name || e.url)
  if (JSON.stringify(envs) !== JSON.stringify(saved.environments)) out.environments = envs
  if ('testing_mission' in out && !out.testing_mission) out.testing_mission = null
  return out
}

function Section({ title, intro, children }: { title: string; intro?: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} style={{ borderTop: '1px solid var(--border-soft)', padding: '22px 0', display: 'grid', gridTemplateColumns: '220px 1fr', gap: 24 }}>
      <div>
        <h2 style={{ fontSize: 14.5, fontWeight: 600, margin: 0 }}>{title}</h2>
        {intro && <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '5px 0 0', lineHeight: 1.5 }}>{intro}</p>}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 560 }}>{children}</div>
    </section>
  )
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={id} style={fieldLabel}>{label}</label>
      {children}
      {hint && <div style={{ fontSize: 11.5, color: 'var(--text-quiet)', marginTop: 5 }}>{hint}</div>}
    </div>
  )
}

function EnvironmentsEditor({ value, onChange }: { value: Environment[]; onChange: (v: Environment[]) => void }) {
  const set = (i: number, patch: Partial<Environment>) => onChange(value.map((e, j) => (j === i ? { ...e, ...patch } : e)))
  return (
    <div>
      <span style={fieldLabel}>Other environments</span>
      {value.length === 0 && <div style={{ fontSize: 12, color: 'var(--text-quiet)', marginBottom: 8 }}>None yet.</div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {value.map((e, i) => (
          <div key={i} style={{ display: 'flex', gap: 8 }}>
            <input aria-label={`Environment ${i + 1} name`} value={e.name} onChange={(ev) => set(i, { name: ev.target.value })}
              placeholder="staging" style={{ ...input, width: 140, flexShrink: 0 }} maxLength={40} />
            <input aria-label={`Environment ${i + 1} URL`} value={e.url} onChange={(ev) => set(i, { url: ev.target.value })}
              placeholder="https://staging.example.com" style={input} />
            <button type="button" aria-label={`Remove environment ${i + 1}`} onClick={() => onChange(value.filter((_, j) => j !== i))}
              style={{ ...quietButton, flexShrink: 0 }}>Remove</button>
          </div>
        ))}
      </div>
      {value.length < 10 && (
        <button type="button" onClick={() => onChange([...value, { name: '', url: '' }])} style={{ ...quietButton, marginTop: 8 }}>
          + Add environment
        </button>
      )}
    </div>
  )
}

export default function ProjectSettings() {
  const { project, setProject } = useProject()
  const [form, setForm] = useState<Form>(() => toForm(project))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const pending = changes(form, toForm(project))
  const dirty = Object.keys(pending).length > 0
  const set = (patch: Partial<Form>) => { setForm((f) => ({ ...f, ...patch })); setSaved(false) }

  async function save(e: React.FormEvent) {
    e.preventDefault()
    if (!form.name.trim()) { setError('A project needs a name.'); return }
    const urls = [form.app_url, ...form.environments.map((x) => x.url)].map((u) => u.trim()).filter(Boolean)
    if (urls.some((u) => !/^https?:\/\/\S+/.test(u))) { setError('Addresses must start with http:// or https://.'); return }
    if (form.environments.some((x) => !x.name.trim() !== !x.url.trim())) { setError('Each environment needs a name and a URL.'); return }
    const names = form.environments.map((x) => x.name.trim().toLowerCase()).filter(Boolean)
    if (new Set(names).size !== names.length) { setError('Environment names must be different.'); return }
    setSaving(true)
    setError(null)
    try {
      const p = await api.updateProject(project.id, pending)
      setProject(p)
      setForm(toForm(p))
      setSaved(true)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1000 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: '0 0 4px' }}>Settings</h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '0 0 22px' }}>For {project.name}.</p>
        <form onSubmit={save} aria-label="Project settings">
          <Section title="Project">
            <Field id="s-name" label="Name">
              <input id="s-name" value={form.name} onChange={(e) => set({ name: e.target.value })} style={input} maxLength={120} />
            </Field>
            <Field id="s-description" label="Description" hint="One line, shown on the Projects page.">
              <input id="s-description" value={form.description} onChange={(e) => set({ description: e.target.value })} style={input} maxLength={300} />
            </Field>
          </Section>

          <Section title="Where the app runs" intro="The recorded browser, the reproduction agent and regression runs start from these, so you don't retype them.">
            <Field id="s-app-url" label="App URL (default)">
              <input id="s-app-url" value={form.app_url} onChange={(e) => set({ app_url: e.target.value })} style={input} placeholder="http://localhost:4000" />
            </Field>
            <EnvironmentsEditor value={form.environments} onChange={(environments) => set({ environments })} />
          </Section>

          <Section title="Testing">
            <Field id="s-mission" label="Testing mission" hint="Steers the product model, risk analysis and charter suggestions.">
              <textarea id="s-mission" value={form.testing_mission} onChange={(e) => set({ testing_mission: e.target.value })}
                rows={2} style={{ ...input, resize: 'vertical' }} placeholder="e.g. Find release blockers in the new document upload flow" />
            </Field>
            <div style={{ display: 'flex', gap: 14 }}>
              <Field id="s-tester" label="Default tester">
                <input id="s-tester" value={form.default_tester} onChange={(e) => set({ default_tester: e.target.value })} style={input} placeholder="Your name" maxLength={60} />
              </Field>
              <Field id="s-provider" label="AI provider">
                <select id="s-provider" value={form.default_ai_provider} onChange={(e) => set({ default_ai_provider: e.target.value })} style={{ ...input, width: 160 }}>
                  {AI_PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </Field>
            </div>
          </Section>

          <Section title="Bug trackers" intro="Where this project's bugs are filed. Tokens and site URLs stay in backend/.env.">
            <Field id="s-github" label="GitHub repo">
              <input id="s-github" value={form.github_repo} onChange={(e) => set({ github_repo: e.target.value })} style={input} placeholder="owner/repo" />
            </Field>
            <Field id="s-jira" label="Jira project key">
              <input id="s-jira" value={form.jira_project_key} onChange={(e) => set({ jira_project_key: e.target.value.toUpperCase() })} style={input} placeholder="KYC" />
            </Field>
          </Section>

          <div style={{ borderTop: '1px solid var(--border-soft)', paddingTop: 18, display: 'flex', alignItems: 'center', gap: 12 }}>
            <button type="submit" disabled={!dirty || saving} style={primaryButton(!dirty || saving)}>{saving ? 'Saving…' : 'Save settings'}</button>
            {dirty && <button type="button" onClick={() => { setForm(toForm(project)); setError(null) }} style={quietButton}>Discard changes</button>}
            {saved && !dirty && <span role="status" style={{ fontSize: 12.5, color: 'var(--accent)' }}>Saved.</span>}
          </div>
          <ErrorLine message={error} />
        </form>
      </main>
    </div>
  )
}
