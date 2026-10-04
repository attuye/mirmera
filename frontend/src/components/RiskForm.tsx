/** The add-a-risk form and the 1-5 score picker, shared by the Risks page and the story page. */
import { useState } from 'react'
import { QUALITY_CRITERIA, RiskInput } from '../api/client'
import { ErrorLine, errorText, fieldLabel, input, primaryButton } from './ui'

export function ScoreSelect({ label, value, onChange, disabled }: { label: string; value: number; onChange: (v: number) => void; disabled: boolean }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11.5, color: 'var(--text-tertiary)' }}>
      {label}
      <select value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))}
        style={{ ...input, width: 50, padding: '3px 6px', fontSize: 12 }}>
        {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
    </label>
  )
}


/** A risk the tester adds by hand: what fails, for whom and when, and its two scores. */
export default function RiskForm({ save: saveRisk, label = 'Add risk' }: { save: (risk: RiskInput) => Promise<void>; label?: string }) {
  const [risk, setRisk] = useState<RiskInput>({ title: '', failure_mode: '', quality_criterion: 'capability', impact: 3, likelihood: 3 })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const valid = risk.title.trim().length >= 3 && risk.failure_mode.trim().length >= 3

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await saveRisk({ ...risk, title: risk.title.trim(), failure_mode: risk.failure_mode.trim() })
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 16, marginBottom: 20, maxWidth: 720 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <div>
          <label htmlFor="risk-title" style={fieldLabel}>Risk</label>
          <input id="risk-title" value={risk.title} onChange={(e) => setRisk({ ...risk, title: e.target.value })}
            placeholder="e.g. Coupon can be reused" style={input} />
        </div>
        <div>
          <label htmlFor="risk-criterion" style={fieldLabel}>Quality criterion</label>
          <select id="risk-criterion" value={risk.quality_criterion} onChange={(e) => setRisk({ ...risk, quality_criterion: e.target.value })} style={input}>
            {QUALITY_CRITERIA.map((q) => <option key={q} value={q}>{q}</option>)}
          </select>
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <label htmlFor="risk-failure" style={fieldLabel}>What fails, for whom, and when</label>
          <input id="risk-failure" value={risk.failure_mode} onChange={(e) => setRisk({ ...risk, failure_mode: e.target.value })}
            placeholder="e.g. A one-time coupon still works on a second order from the same shopper" style={input} />
        </div>
      </div>
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', marginTop: 12 }}>
        <ScoreSelect label="Impact" value={risk.impact} disabled={busy} onChange={(v) => setRisk({ ...risk, impact: v })} />
        <ScoreSelect label="Likelihood" value={risk.likelihood} disabled={busy} onChange={(v) => setRisk({ ...risk, likelihood: v })} />
        <button onClick={save} disabled={!valid || busy} style={{ ...primaryButton(!valid || busy), marginLeft: 'auto' }}>
          {busy ? 'Adding…' : label}
        </button>
      </div>
      <ErrorLine message={error} />
    </div>
  )
}
