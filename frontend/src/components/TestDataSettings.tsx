/** The project's test data: values the team keeps for the app under test (a valid national ID, a
 *  test phone number, one-use vouchers) and which of the app's fields each fills. Runs fill those
 *  fields from here before making a value up; invalid and edge values become test ideas. Secrets are
 *  encrypted, never shown again, and never shown to the AI. */
import { useEffect, useState } from 'react'
import { api, KnownField, TestDataInput, TestDataRow } from '../api/client'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

const EMPTY: TestDataInput = { name: '', value: '', secret: false, labels: [], page: '', kind: 'valid', once: false, note: '' }
const KIND_LABEL: Record<TestDataRow['kind'], string> = {
  valid: 'Valid: fills the field', invalid: 'Invalid: a test idea', edge: 'Edge case: a test idea',
}
const chip: React.CSSProperties = {
  fontSize: 11, padding: '2px 7px', borderRadius: 5, border: '1px solid var(--border)', color: 'var(--text-secondary)',
}

/** Which of the app's fields a value fills: picked from the fields Mirmera has met, or typed. */
function FieldPicker({ known, value, onChange }: { known: KnownField[]; value: string[]; onChange: (v: string[]) => void }) {
  const [typed, setTyped] = useState('')
  const toggle = (label: string) => onChange(value.includes(label) ? value.filter((x) => x !== label) : [...value, label])
  const byPage = new Map<string, KnownField[]>()
  for (const f of known) {
    const page = f.pages[0] ?? 'Other'
    byPage.set(page, [...(byPage.get(page) ?? []), f])
  }
  return (
    <fieldset aria-label="Fields it fills" style={{ border: 'none', padding: 0, margin: 0 }}>
      <legend style={fieldLabel}>Fields it fills</legend>
      {known.length === 0 && (
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>
          No fields met yet: survey the app (Coverage) or let Mirmera explore, then pick them here. Or type a field's label.
        </div>
      )}
      {[...byPage.entries()].map(([page, fields]) => (
        <div key={page} style={{ marginBottom: 6 }}>
          <div className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)' }}>{page}</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 3 }}>
            {fields.map((f) => (
              <label key={f.label} style={{ ...chip, display: 'inline-flex', gap: 5, alignItems: 'center', cursor: 'pointer',
                borderColor: value.includes(f.label) ? 'var(--accent)' : 'var(--border)' }}>
                <input type="checkbox" checked={value.includes(f.label)} onChange={() => toggle(f.label)} />{f.label}
              </label>
            ))}
          </div>
        </div>
      ))}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
        {value.filter((v) => !known.some((k) => k.label === v)).map((v) => (
          <span key={v} style={chip}>{v} <button type="button" aria-label={`Remove ${v}`} onClick={() => toggle(v)}
            style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0 }}>×</button></span>
        ))}
        <input aria-label="Another field's label" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Another field's label"
          style={{ ...input, width: 200 }}
          onKeyDown={(e) => { if (e.key === 'Enter' && typed.trim()) { e.preventDefault(); onChange([...value, typed.trim()]); setTyped('') } }} />
      </div>
    </fieldset>
  )
}

function RowForm({ projectId, known, editing, onDone, onCancel }: {
  projectId: string; known: KnownField[]; editing: TestDataRow | null
  onDone: (row: TestDataRow) => void; onCancel: () => void
}) {
  const [form, setForm] = useState<TestDataInput>(editing
    ? { name: editing.name, value: editing.secret ? '' : editing.value, secret: editing.secret, labels: editing.labels,
        page: editing.page ?? '', kind: editing.kind, once: editing.once, note: editing.note ?? '' }
    : EMPTY)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = <K extends keyof TestDataInput>(k: K, v: TestDataInput[K]) => setForm((f) => ({ ...f, [k]: v }))

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setError(null)
    const body = { ...form, page: form.page || null, note: form.note || null,
      value: editing && editing.secret && !form.value ? null : form.value }  // a secret kept as it is unless retyped
    try {
      onDone(editing ? await api.changeTestData(projectId, editing.id, body) : await api.addTestData(projectId, body))
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  return (
    <form aria-label={editing ? `Change ${editing.name}` : 'Add test data'} onSubmit={save}
      style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <label style={{ flex: 1, minWidth: 180 }}><span style={fieldLabel}>Name</span>
          <input value={form.name} onChange={(e) => set('name', e.target.value)} style={input} placeholder="valid national ID" /></label>
        <label style={{ flex: 1, minWidth: 180 }}><span style={fieldLabel}>Value</span>
          <input value={form.value ?? ''} onChange={(e) => set('value', e.target.value)} style={input}
            type={form.secret ? 'password' : 'text'} autoComplete="off"
            placeholder={editing?.secret ? 'Kept: type to replace it' : 'ET-4471-0093'} /></label>
        <label style={{ minWidth: 200 }}><span style={fieldLabel}>Kind</span>
          <select value={form.kind} onChange={(e) => set('kind', e.target.value as TestDataRow['kind'])} style={input}>
            {(Object.keys(KIND_LABEL) as TestDataRow['kind'][]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
          </select></label>
      </div>
      <FieldPicker known={known} value={form.labels} onChange={(v) => set('labels', v)} />
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 12.5, color: 'var(--text-secondary)' }}>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={form.once} onChange={(e) => set('once', e.target.checked)} />
          Works once (a voucher, a phone number for one sign-up)</label>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={form.secret} onChange={(e) => set('secret', e.target.checked)} />
          Secret: encrypted, never shown again, never shown to the AI</label>
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <label style={{ flex: 1, minWidth: 180 }}><span style={fieldLabel}>Only on this page (optional)</span>
          <input value={form.page ?? ''} onChange={(e) => set('page', e.target.value)} style={input} placeholder="/verify" /></label>
        <label style={{ flex: 2, minWidth: 220 }}><span style={fieldLabel}>Note (optional)</span>
          <input value={form.note ?? ''} onChange={(e) => set('note', e.target.value)} style={input} placeholder="Abebe's test account" /></label>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button type="submit" disabled={busy || !form.name.trim() || (!editing && !form.value)} style={primaryButton(busy)}>
          {busy ? 'Saving…' : editing ? 'Save' : 'Add'}</button>
        <button type="button" onClick={onCancel} style={quietButton}>Cancel</button>
      </div>
      <ErrorLine message={error} />
    </form>
  )
}

export default function TestDataSettings({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<TestDataRow[] | null>(null)
  const [known, setKnown] = useState<KnownField[]>([])
  const [editing, setEditing] = useState<TestDataRow | 'new' | null>(null)
  const [csv, setCsv] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.testData(projectId).then(setRows).catch((e) => setError(errorText(e)))
    api.knownFields(projectId).then(setKnown).catch(() => {})
  }, [projectId])
  if (!rows) return <ErrorLine message={error} />

  const put = (row: TestDataRow) => setRows((rs) => rs!.some((r) => r.id === row.id) ? rs!.map((r) => (r.id === row.id ? row : r)) : [...rs!, row])
  const onceLeft = (r: TestDataRow) => rows.filter((x) => x.once && !x.used_at && x.labels.some((l) => r.labels.includes(l))).length

  return (
    <section aria-label="Test data" style={{ borderTop: '1px solid var(--border-soft)', padding: '22px 0', display: 'grid', gridTemplateColumns: '220px 1fr', gap: 24 }}>
      <div>
        <h2 style={{ fontSize: 14.5, fontWeight: 600, margin: 0 }}>Test data</h2>
        <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '5px 0 0', lineHeight: 1.5 }}>
          Values the app really accepts, and the ones you know are wrong. Runs fill these fields from here before making a value
          up; invalid and edge values become test ideas. In a test's steps, <span className="mono">{'{data:Name}'}</span> stands for a value.
        </p>
      </div>
      <div style={{ minWidth: 0 }}>
      {rows.length > 0 && (
        <ul aria-label="Test data rows" style={{ listStyle: 'none', margin: '0 0 10px', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {rows.map((r) => (
            <li key={r.id} aria-label={r.name} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', fontSize: 12.5 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <strong style={{ fontWeight: 600 }}>{r.name}</strong>
                <span className="mono" style={{ color: 'var(--text-secondary)' }}>{r.secret ? '••• (secret)' : r.value}</span>
                <span style={chip}>{r.kind}</span>
                {r.once && <span style={chip}>{r.used_at ? `used ${new Date(r.used_at).toLocaleDateString()}` : `once · ${onceLeft(r)} left`}</span>}
                <span style={{ flex: 1 }} />
                {r.once && r.used_at && <button style={{ ...quietButton, fontSize: 11, padding: '2px 8px' }}
                  onClick={async () => { try { put(await api.resetTestData(projectId, r.id)) } catch (e) { setError(errorText(e)) } }}>Usable again</button>}
                <button style={{ ...quietButton, fontSize: 11, padding: '2px 8px' }} onClick={() => setEditing(r)}>Change</button>
                <button style={{ ...quietButton, fontSize: 11, padding: '2px 8px' }} onClick={async () => {
                  try { await api.deleteTestData(projectId, r.id); setRows(rows.filter((x) => x.id !== r.id)) } catch (e) { setError(errorText(e)) }
                }}>Delete</button>
              </div>
              <div style={{ color: 'var(--text-tertiary)', fontSize: 11.5, marginTop: 3 }}>
                Fills: {r.labels.join(', ') || 'no field yet'}{r.page ? ` (on ${r.page})` : ''}{r.note ? ` · ${r.note}` : ''}
              </div>
              {r.not_seen && (
                <div role="alert" style={{ color: 'var(--warning)', fontSize: 11.5, marginTop: 3 }}>
                  The last run saw forms but no {r.labels.map((l) => `"${l}"`).join(' or ') || 'field for it'}: renamed? Change it to pick the new field.
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {editing ? (
        <RowForm projectId={projectId} known={known} editing={editing === 'new' ? null : editing}
          onDone={(row) => { put(row); setEditing(null) }} onCancel={() => setEditing(null)} />
      ) : csv !== null ? (
        <form aria-label="Import CSV" onSubmit={async (e) => {
          e.preventDefault()
          try { const made = await api.importTestData(projectId, csv); setRows([...rows, ...made]); setCsv(null) } catch (err) { setError(errorText(err)) }
        }} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <label><span style={fieldLabel}>CSV: name,value,labels,kind,once,secret,page,note (labels joined with |)</span>
            <textarea value={csv} onChange={(e) => setCsv(e.target.value)} rows={6} style={{ ...input, fontFamily: 'var(--font-mono)' }}
              placeholder={'name,value,labels,kind,once\nvoucher 1,V-1001,Voucher code,valid,yes'} /></label>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="submit" disabled={!csv.trim()} style={primaryButton(!csv.trim())}>Import</button>
            <button type="button" onClick={() => setCsv(null)} style={quietButton}>Cancel</button>
          </div>
        </form>
      ) : (
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setEditing('new')} style={quietButton}>Add test data</button>
          <button onClick={() => setCsv('')} style={quietButton}>Import CSV</button>
        </div>
      )}
      <ErrorLine message={error} />
      </div>
    </section>
  )
}
