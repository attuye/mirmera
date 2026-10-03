/** Steps to the bug: what the regression test and the reproduction agent replay.
 *  Mirmera proposes them from this bug's own recording; the tester can remove,
 *  reorder, change and add steps, and saved steps are used exactly as they are. */
import { useEffect, useState } from 'react'
import { api, BugListItem, BugStep, BugSteps, StepAction } from '../api/client'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

type Kind = 'goto' | 'click' | 'fill' | 'secret' | 'select' | 'check' | 'uncheck' | 'press'
type Method = 'label' | 'role' | 'text' | 'placeholder' | 'testid'

const KINDS: { kind: Kind; label: string }[] = [
  { kind: 'goto', label: 'Open a page' }, { kind: 'click', label: 'Click' }, { kind: 'fill', label: 'Fill a field' },
  { kind: 'secret', label: 'Fill a secret' }, { kind: 'select', label: 'Choose an option' }, { kind: 'check', label: 'Tick' },
  { kind: 'uncheck', label: 'Untick' }, { kind: 'press', label: 'Press a key' },
]
const METHODS: { method: Method; label: string; hint: string }[] = [
  { method: 'label', label: 'Field label', hint: 'Full name' }, { method: 'role', label: 'Button / link / …', hint: 'Verify' },
  { method: 'text', label: 'Visible text', hint: 'Start verification →' }, { method: 'placeholder', label: 'Placeholder', hint: '+251 9xx xxx xxx' },
  { method: 'testid', label: 'Test id', hint: 'submit' },
]
const ROLES = ['button', 'link', 'checkbox', 'radio', 'textbox', 'combobox', 'tab', 'menuitem', 'option']
const KEYS = ['Enter', 'Tab', 'Escape', 'ArrowDown', 'ArrowUp', 'Space']

const SOURCE: Record<BugSteps['source'], { label: string; fg: string; bg: string }> = {
  recording: { label: 'Picked from the recording', fg: 'var(--accent)', bg: 'var(--accent-bg)' },
  edited: { label: 'Edited by you', fg: 'var(--info)', bg: '#16202E' },
  ai: { label: 'The AI picks from these', fg: 'var(--warning)', bg: 'var(--warning-bg)' },
}

function target(a: StepAction): string {
  return a.locator.method === 'role' ? `${a.locator.role} "${a.locator.name ?? ''}"` : `${a.locator.method} "${a.locator.value ?? ''}"`
}

/** Same wording as the server's describe_action, so edited steps read like recorded ones. */
export function describe(step: BugStep): string {
  if (step.type === 'goto') return `open ${step.url}`
  const a = step.action!
  if (a.action === 'fill') return `fill ${target(a)} = ` + (a.masked ? `(hidden: ${a.secret})` : `"${a.value ?? ''}"`)
  if (a.action === 'select') return `select "${a.value ?? ''}" in ${target(a)}`
  if (a.action === 'press') return `press ${a.key ?? 'Enter'} in ${target(a)}`
  if (a.action === 'upload') return `upload a file to ${target(a)}`
  return `${a.action} ${target(a)}`
}

interface Form { kind: Kind; method: Method; role: string; name: string; value: string; text: string; secret: string; key: string; url: string }

const EMPTY: Form = { kind: 'click', method: 'role', role: 'button', name: '', value: '', text: '', secret: 'MIRMERA_SECRET_PASSWORD', key: 'Enter', url: '' }

function toForm(step: BugStep): Form {
  if (step.type === 'goto') return { ...EMPTY, kind: 'goto', url: step.url ?? '' }
  const a = step.action!
  const kind: Kind = a.action === 'fill' && a.masked ? 'secret' : (a.action as Kind)
  const m = (a.locator.method === 'css' ? 'text' : a.locator.method) as Method
  return { ...EMPTY, kind, method: m, role: a.locator.role ?? 'button', name: a.locator.name ?? '', value: a.locator.value ?? '',
    text: a.value ?? '', secret: a.secret ?? EMPTY.secret, key: a.key ?? 'Enter' }
}

/** Builds the step, or says what is missing. */
function fromForm(f: Form): BugStep | string {
  if (f.kind === 'goto') {
    return /^https?:\/\/\S+/.test(f.url.trim()) ? { type: 'goto', url: f.url.trim() } : 'The page must be a full address, e.g. http://localhost:4000/verify.'
  }
  const locator = f.method === 'role' ? { method: 'role' as const, role: f.role, name: f.name.trim() } : { method: f.method, value: f.value.trim() }
  if (f.method === 'role' ? !f.name.trim() : !f.value.trim()) return 'Say which element: its label, name or text.'
  const action: StepAction = { action: f.kind === 'secret' ? 'fill' : f.kind, locator }
  if (f.kind === 'fill' || f.kind === 'select') {
    if (!f.text.trim() && f.kind === 'select') return 'Say which option to choose.'
    action.value = f.text
  }
  if (f.kind === 'secret') {
    const name = f.secret.trim().toUpperCase().replace(/[^A-Z0-9_]/g, '_')
    if (!/^MIRMERA_SECRET_[A-Z0-9_]+$/.test(name)) return 'A secret is named MIRMERA_SECRET_SOMETHING, set in the backend environment.'
    Object.assign(action, { masked: true, secret: name })
  }
  if (f.kind === 'press') action.key = f.key
  const step: BugStep = { type: 'action', action }
  return { ...step, summary: describe(step) }
}

function StepForm({ initial, onDone, onCancel, title, positions }: {
  initial: Form; title: string; onDone: (s: BugStep, at: number) => void; onCancel: () => void
  positions?: { value: number; label: string }[]
}) {
  const [f, setF] = useState<Form>(initial)
  const [at, setAt] = useState(positions ? positions[positions.length - 1].value : -1)
  const [error, setError] = useState<string | null>(null)
  const set = (patch: Partial<Form>) => { setF((x) => ({ ...x, ...patch })); setError(null) }
  const small = { ...input, padding: '6px 9px', fontSize: 12.5 }
  const hint = METHODS.find((m) => m.method === f.method)?.hint

  function submit(e: React.FormEvent) {
    e.preventDefault()
    const step = fromForm(f)
    if (typeof step === 'string') { setError(step); return }
    onDone(step, at)
  }

  return (
    <form aria-label={title} onSubmit={submit} style={{ border: '1px solid var(--border)', borderRadius: 9, padding: '12px 14px', margin: '8px 0', background: 'var(--panel-alt)' }}>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={{ width: 150 }}>
          <span style={fieldLabel}>Step</span>
          <select value={f.kind} onChange={(e) => set({ kind: e.target.value as Kind })} style={small}>
            {KINDS.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
          </select>
        </label>
        {f.kind === 'goto' ? (
          <label style={{ flex: 1, minWidth: 240 }}>
            <span style={fieldLabel}>Address</span>
            <input value={f.url} onChange={(e) => set({ url: e.target.value })} placeholder="http://localhost:4000/verify" style={small} />
          </label>
        ) : (
          <>
            <label style={{ width: 160 }}>
              <span style={fieldLabel}>Find it by</span>
              <select value={f.method} onChange={(e) => set({ method: e.target.value as Method })} style={small}>
                {METHODS.map((m) => <option key={m.method} value={m.method}>{m.label}</option>)}
              </select>
            </label>
            {f.method === 'role' && (
              <label style={{ width: 120 }}>
                <span style={fieldLabel}>Role</span>
                <select value={f.role} onChange={(e) => set({ role: e.target.value })} style={small}>
                  {ROLES.map((r) => <option key={r}>{r}</option>)}
                </select>
              </label>
            )}
            <label style={{ flex: 1, minWidth: 160 }}>
              <span style={fieldLabel}>{f.method === 'role' ? 'Name' : 'Which one'}</span>
              <input value={f.method === 'role' ? f.name : f.value} placeholder={hint}
                onChange={(e) => set(f.method === 'role' ? { name: e.target.value } : { value: e.target.value })} style={small} />
            </label>
            {(f.kind === 'fill' || f.kind === 'select') && (
              <label style={{ flex: 1, minWidth: 140 }}>
                <span style={fieldLabel}>{f.kind === 'fill' ? 'Text to type' : 'Option'}</span>
                <input value={f.text} onChange={(e) => set({ text: e.target.value })} style={small} />
              </label>
            )}
            {f.kind === 'secret' && (
              <label style={{ flex: 1, minWidth: 220 }}>
                <span style={fieldLabel}>Secret (set in the backend environment)</span>
                <input value={f.secret} onChange={(e) => set({ secret: e.target.value })} style={small} />
              </label>
            )}
            {f.kind === 'press' && (
              <label style={{ width: 120 }}>
                <span style={fieldLabel}>Key</span>
                <select value={f.key} onChange={(e) => set({ key: e.target.value })} style={small}>
                  {KEYS.map((k) => <option key={k}>{k}</option>)}
                </select>
              </label>
            )}
          </>
        )}
        {positions && (
          <label style={{ width: 150 }}>
            <span style={fieldLabel}>Where</span>
            <select value={at} onChange={(e) => setAt(Number(e.target.value))} style={small}>
              {positions.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
          </label>
        )}
      </div>
      <ErrorLine message={error} />
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        <button type="submit" style={{ ...primaryButton(), padding: '6px 12px' }}>{positions ? 'Add step' : 'Done'}</button>
        <button type="button" onClick={onCancel} style={quietButton}>Cancel</button>
      </div>
    </form>
  )
}

export default function BugStepsPanel({ bug }: { bug: BugListItem }) {
  const [data, setData] = useState<BugSteps | null>(null)
  const [steps, setSteps] = useState<BugStep[]>([])
  const [dirty, setDirty] = useState(false)
  const [editing, setEditing] = useState<number | null>(null)
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)

  function load(d: BugSteps) { setData(d); setSteps(d.steps); setDirty(false); setEditing(null); setAdding(false) }
  useEffect(() => { api.getBugSteps(bug.id).then(load).catch((e) => setError(errorText(e))) }, [bug.id])

  function change(next: BugStep[]) { setSteps(next); setDirty(true); setSaved(false); setError(null) }
  const move = (i: number, by: number) => {
    const next = [...steps]; const [s] = next.splice(i, 1); next.splice(i + by, 0, s); change(next)
  }

  async function act(fn: () => Promise<BugSteps>, savedNow: boolean) {
    setBusy(true); setError(null)
    try { load(await fn()); setSaved(savedNow) } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const payload = () => steps.map(({ type, url, action }) => (type === 'goto' ? { type, url } : { type, action }))

  if (!data) return error ? <ErrorLine message={error} /> : null
  const src = SOURCE[dirty ? 'edited' : data.source]
  const small = { ...quietButton, padding: '2px 7px', fontSize: 11 }

  return (
    <section aria-label="Steps to the bug" style={{ marginTop: 18, border: '1px solid var(--border)', borderRadius: 10, padding: '14px 16px' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button onClick={() => setOpen((o) => !o)} aria-expanded={open} style={{ background: 'none', color: 'var(--text)', padding: 0, fontSize: 13.5, fontWeight: 600 }}>
          {open ? '▾' : '▸'} Steps to the bug <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>({steps.length})</span>
        </button>
        <span style={{ fontSize: 11.5, fontWeight: 600, color: src.fg, background: src.bg, padding: '2px 8px', borderRadius: 6 }}>
          {dirty ? 'Unsaved changes' : src.label}
        </span>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '6px 0 0', lineHeight: 1.5 }}>
        {data.note} The regression test and the reproduction agent replay these.
      </p>

      {open && (
        <>
          <ol style={{ margin: '10px 0 0', padding: 0 }}>
            {steps.map((s, i) => (
              <li key={i} aria-label={s.summary ?? describe(s)} style={{ listStyle: 'none', borderTop: '1px solid var(--border-soft)', padding: '5px 0' }}>
                {editing === i ? (
                  <StepForm title={`Edit step ${i + 1}`} initial={toForm(s)} onCancel={() => setEditing(null)}
                    onDone={(step) => { change(steps.map((x, j) => (j === i ? step : x))); setEditing(null) }} />
                ) : (
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)', width: 22, flexShrink: 0 }}>{i + 1}.</span>
                    <span className="mono" style={{ fontSize: 12, color: 'var(--text-secondary)', flex: 1, minWidth: 0, wordBreak: 'break-word' }}>
                      {s.summary ?? describe(s)}
                    </span>
                    <button aria-label={`Move step ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)} style={small}>↑</button>
                    <button aria-label={`Move step ${i + 1} down`} disabled={i === steps.length - 1} onClick={() => move(i, 1)} style={small}>↓</button>
                    {s.action?.action !== 'upload' && <button aria-label={`Edit step ${i + 1}`} onClick={() => setEditing(i)} style={small}>Edit</button>}
                    <button aria-label={`Remove step ${i + 1}`} onClick={() => change(steps.filter((_, j) => j !== i))} style={small}>Remove</button>
                  </div>
                )}
              </li>
            ))}
          </ol>
          {steps.length === 0 && <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 8 }}>No steps. Add them below.</div>}

          {adding ? (
            <StepForm title="New step" initial={EMPTY} onCancel={() => setAdding(false)}
              positions={[{ value: 0, label: 'At the start' }, ...steps.map((_, i) => ({ value: i + 1, label: `After step ${i + 1}` }))]}
              onDone={(step, at) => { const next = [...steps]; next.splice(at, 0, step); change(next); setAdding(false) }} />
          ) : (
            <button onClick={() => setAdding(true)} style={{ ...quietButton, marginTop: 10 }}>+ Add step</button>
          )}

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
            <button disabled={!dirty || busy} onClick={() => act(() => api.saveBugSteps(bug.id, payload()), true)} style={primaryButton(!dirty || busy)}>
              {busy ? 'Saving…' : 'Save steps'}
            </button>
            {dirty && <button onClick={() => { setSteps(data.steps); setDirty(false); setError(null) }} style={quietButton}>Discard changes</button>}
            {data.source === 'edited' && !dirty && (
              <button disabled={busy} onClick={() => act(() => api.resetBugSteps(bug.id), false)} style={quietButton}>Go back to the recording</button>
            )}
            {saved && !dirty && (
              <span role="status" style={{ fontSize: 12, color: 'var(--accent)' }}>
                Saved. Generate the regression test again to use these steps.
              </span>
            )}
          </div>
        </>
      )}
      <ErrorLine message={error} />
    </section>
  )
}
