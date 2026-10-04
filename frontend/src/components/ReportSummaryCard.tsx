/** The report's opening summary, drafted by AI on request from the report's own
 *  facts, then kept, edited or thrown away by the tester. Always labelled. */
import { useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { api, ReportSummary, ReportSummaryDraft } from '../api/client'
import { ErrorLine, errorText, input, primaryButton, quietButton } from './ui'

const box: React.CSSProperties = { background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, padding: '14px 18px', marginTop: 16 }
const who = (p: string | null, m: string | null) => [p, m].filter(Boolean).join(' · ')

export default function ReportSummaryCard({ projectId, since, until, kept, onChange }: {
  projectId: string; since?: string; until?: string; kept: ReportSummary | null; onChange: () => void
}) {
  const [draft, setDraft] = useState<ReportSummaryDraft | null>(null)
  const [text, setText] = useState('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { setDraft(null); setEditing(false); setError(null) }, [projectId, since, until])

  async function run(what: string, fn: () => Promise<unknown>) {
    setBusy(what); setError(null)
    try { await fn() } catch (e) { setError(errorText(e)) } finally { setBusy(null) }
  }
  const ask = () => run('draft', async () => {
    const d = await api.draftReportSummary(projectId, since, until)
    setDraft(d); setText(d.text); setEditing(false)
  })
  const keep = () => run('keep', async () => {
    const source = draft ?? kept!
    await api.keepReportSummary(projectId, {
      text, ai_provider: source.ai_provider, ai_model: source.ai_model, prompt_version: source.prompt_version,
      edited: draft ? text.trim() !== draft.text.trim() : true,  // editing a kept summary changes it
    }, since, until)
    setDraft(null); setEditing(false); onChange()
  })
  const remove = () => run('remove', async () => { await api.removeReportSummary(projectId, since, until); onChange() })

  const label = (p: string | null, m: string | null, extra: string) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--text-tertiary)' }}>
      <Sparkles size={13} strokeWidth={1.8} color="var(--question)" aria-hidden />
      Drafted by AI{who(p, m) ? ` (${who(p, m)})` : ''}{extra}
    </div>
  )

  if (draft || editing) {
    const source = draft ?? kept!
    return (
      <section aria-label="Summary draft" style={{ ...box, borderColor: 'var(--question)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
          <h2 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Summary</h2>
          {label(source.ai_provider, source.ai_model, draft ? ': review before keeping' : '')}
        </div>
        <textarea aria-label="Summary text" value={text} onChange={(e) => setText(e.target.value)} rows={4}
          style={{ ...input, width: '100%', resize: 'vertical', lineHeight: 1.5, fontSize: 13 }} />
        {draft && draft.basis.length > 0 && (
          <details style={{ marginTop: 6, fontSize: 11.5, color: 'var(--text-tertiary)' }}>
            <summary style={{ cursor: 'pointer' }}>Based on {draft.basis.length} line{draft.basis.length === 1 ? '' : 's'} of this report</summary>
            <ul style={{ margin: '4px 0 0', paddingLeft: 18, lineHeight: 1.6 }}>{draft.basis.map((b) => <li key={b}>{b}</li>)}</ul>
          </details>
        )}
        <ErrorLine message={error} />
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <button onClick={keep} disabled={!!busy || !text.trim()} style={primaryButton(!!busy || !text.trim())}>{busy === 'keep' ? 'Keeping…' : 'Keep'}</button>
          {draft && <button onClick={ask} disabled={!!busy} style={quietButton}>{busy === 'draft' ? 'Drafting…' : 'Draft again'}</button>}
          <button onClick={() => { setDraft(null); setEditing(false) }} disabled={!!busy} style={quietButton}>{draft ? 'Discard' : 'Cancel'}</button>
        </div>
      </section>
    )
  }

  if (kept) {
    return (
      <section aria-label="Summary" style={box}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
          <h2 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Summary</h2>
          {label(kept.ai_provider, kept.ai_model, kept.edited ? ', edited by you' : ', kept by you')}
        </div>
        <p style={{ fontSize: 13.5, lineHeight: 1.6, margin: 0 }}>{kept.text}</p>
        <ErrorLine message={error} />
        <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
          <button onClick={() => { setText(kept.text); setEditing(true) }} style={{ background: 'none', padding: 0, fontSize: 12, color: 'var(--text-secondary)' }}>Edit</button>
          <button onClick={ask} disabled={!!busy} style={{ background: 'none', padding: 0, fontSize: 12, color: 'var(--text-secondary)' }}>{busy === 'draft' ? 'Drafting…' : 'Draft again'}</button>
          <button onClick={remove} disabled={!!busy} style={{ background: 'none', padding: 0, fontSize: 12, color: 'var(--text-tertiary)' }}>Remove</button>
        </div>
      </section>
    )
  }

  return (
    <div style={{ marginTop: 14 }}>
      <button onClick={ask} disabled={!!busy} style={{ ...quietButton, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <Sparkles size={14} strokeWidth={1.8} color="var(--question)" aria-hidden />
        {busy === 'draft' ? 'Drafting a summary…' : 'Draft summary with AI'}
      </button>
      <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginLeft: 10 }}>
        From this report's own numbers; you review it before it's kept.
      </span>
      <ErrorLine message={error} />
    </div>
  )
}
