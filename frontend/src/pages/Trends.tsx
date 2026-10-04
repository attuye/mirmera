/** Testing trends: how the project's testing moves week by week, and what changed
 *  over the last four weeks. Counted from what was recorded. */
import { useEffect, useState } from 'react'
import { Minus, TrendingDown, TrendingUp } from 'lucide-react'
import Sidebar from '../components/Sidebar'
import WeekChart from '../components/WeekChart'
import { ErrorLine, errorText, quietButton } from '../components/ui'
import { api, Trends } from '../api/client'
import { useProject } from '../project'

// Categorical slots 1–4 (validated on this dark surface): blue, orange, aqua, yellow.
// Stacked in this order so neighbours are the validated adjacent pairs (yellow never touches orange).
const PHASE_ORDER = ['on_charter', 'bug_investigation', 'setup', 'opportunity']
const PHASE_COLORS: Record<string, string> = { on_charter: '#3987e5', bug_investigation: '#d95926', setup: '#199e70', opportunity: '#c98500' }
const RANGES = [8, 12, 26]
const hours = (v: number) => (v >= 10 ? `${Math.round(v)} h` : `${Math.round(v * 10) / 10} h`)

function Changes({ trends }: { trends: Trends }) {
  if (!trends.changes.length) return null
  return (
    <section aria-label="What changed" style={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, padding: '12px 16px', marginTop: 16 }}>
      <h2 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 6px' }}>
        What changed <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--text-tertiary)' }}>last 4 weeks against the 4 before</span>
      </h2>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {trends.changes.map((c) => {
          const Icon = c.direction === 'up' ? TrendingUp : c.direction === 'down' ? TrendingDown : Minus
          return (
            <li key={c.name} aria-label={c.name} style={{ display: 'flex', gap: 10, padding: '5px 0', fontSize: 13, alignItems: 'baseline' }}>
              <Icon size={15} strokeWidth={1.8} color="var(--text-secondary)" aria-label={c.direction} style={{ flexShrink: 0, alignSelf: 'center' }} />
              <span>{c.text}{c.note && <span style={{ color: 'var(--text-tertiary)', fontSize: 12 }}> · {c.note}</span>}</span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function Tables({ trends }: { trends: Trends }) {
  const cols: [string, (r: Trends['rows'][number]) => string][] = [
    ['Week of', (r) => r.week], ['Sessions', (r) => `${r.sessions}`], ['Hours', (r) => `${r.hours}`], ['Testers', (r) => `${r.testers}`],
    ['Bugs (high/medium/low)', (r) => `${r.bugs.high}/${r.bugs.medium}/${r.bugs.low}`], ['Questions', (r) => `${r.questions}`],
    ['Bugs per hour', (r) => (r.bugs_per_hour === null ? '–' : `${r.bugs_per_hour}`)],
    ['Charters (done/partly/not)', (r) => `${r.charters.fulfilled}/${r.charters.partly}/${r.charters.not}`],
    ['Fixes (verified/still fail)', (r) => `${r.fixes.verified}/${r.fixes.still_fails}`],
    ['Risk exposure', (r) => (r.risk_exposure === null ? '–' : `${r.risk_exposure}`)],
  ]
  return (
    <div style={{ overflowX: 'auto', marginTop: 16 }}>
      <table aria-label="Trends table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
        <thead><tr style={{ color: 'var(--text-tertiary)', textAlign: 'left' }}>{cols.map(([h]) => <th key={h} style={{ fontWeight: 500, padding: '4px 6px' }}>{h}</th>)}</tr></thead>
        <tbody>{trends.rows.map((r) => <tr key={r.week} style={{ borderTop: '1px solid var(--border-soft)' }}>{cols.map(([h, f]) => <td key={h} className="mono" style={{ padding: '6px', fontSize: 11.5 }}>{f(r)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

export default function TrendsPage() {
  const { project } = useProject()
  const [weeks, setWeeks] = useState(12)
  const [trends, setTrends] = useState<Trends | null>(null)
  const [asTable, setAsTable] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.getTrends(project.id, weeks).then(setTrends).catch((e) => setError(errorText(e))) }, [project.id, weeks])

  const rows = trends?.rows ?? []
  const w = rows.map((r) => r.week)
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1200 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Trends</h1>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 640, lineHeight: 1.5 }}>
              How the testing of {project.name} moves, week by week (Monday to Sunday). The report shows one period; this shows the direction.
            </p>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <div role="group" aria-label="Weeks" style={{ display: 'flex', gap: 6 }}>
              {RANGES.map((n) => (
                <button key={n} aria-pressed={weeks === n} onClick={() => setWeeks(n)}
                  style={{ ...quietButton, fontSize: 12, padding: '5px 10px', ...(weeks === n ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}) }}>{n} weeks</button>
              ))}
            </div>
            <button aria-pressed={asTable} onClick={() => setAsTable(!asTable)} style={{ ...quietButton, fontSize: 12, padding: '5px 10px' }}>
              {asTable ? 'Show charts' : 'Show as table'}
            </button>
          </div>
        </div>
        <ErrorLine message={error} />
        {trends && <Changes trends={trends} />}
        {trends && (asTable ? <Tables trends={trends} /> : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))', gap: 14, marginTop: 16 }}>
            <WeekChart title="Testing time" note="Hours in sessions, pauses left out" weeks={w} kind="columns" format={hours}
              series={[{ key: 'hours', label: 'Hours', color: '#3987e5', values: rows.map((r) => r.hours) }]}
              extra={(i) => [`${rows[i].sessions} session${rows[i].sessions === 1 ? '' : 's'} · ${rows[i].testers} tester${rows[i].testers === 1 ? '' : 's'}`]} />
            <WeekChart title="Bugs found" note="Reviewed bugs, by severity" weeks={w} kind="stacked" integer
              series={[
                { key: 'high', label: 'High', color: 'var(--danger)', values: rows.map((r) => r.bugs.high) },
                { key: 'medium', label: 'Medium', color: 'var(--warning)', values: rows.map((r) => r.bugs.medium) },
                { key: 'low', label: 'Low', color: '#6b7280', values: rows.map((r) => r.bugs.low) },
              ]}
              extra={(i) => [rows[i].questions ? `${rows[i].questions} question${rows[i].questions === 1 ? '' : 's'}` : '', rows[i].unreviewed ? `${rows[i].unreviewed} not reviewed` : ''].filter(Boolean)} />
            <WeekChart title="Bugs per hour of testing" note="Weeks with under 10 minutes of testing are left out" weeks={w} kind="line"
              format={(v) => `${Math.round(v * 100) / 100}`}
              series={[{ key: 'rate', label: 'Bugs per hour', color: '#3987e5', values: rows.map((r) => r.bugs_per_hour) }]} />
            <WeekChart title="Where the time went" note="From session notes, in debriefed sessions" weeks={w} kind="percent"
              series={PHASE_ORDER.filter((k) => k in trends.phases).map((k) => ({ key: k, label: String(trends.phases[k]), color: PHASE_COLORS[k], values: rows.map((r) => r.time_split[k] ?? 0) }))} />
            <WeekChart title="Fixes checked after the issue closed" note="Retests: verified, or the bug still reproduced" weeks={w} kind="stacked" integer
              series={[
                { key: 'verified', label: 'Fix verified', color: 'var(--accent)', values: rows.map((r) => r.fixes.verified) },
                { key: 'still', label: 'Still fails', color: 'var(--danger)', values: rows.map((r) => r.fixes.still_fails) },
              ]} />
            <WeekChart title="Risk exposure" note="Sum of the accepted risks' scores at the end of each week" weeks={w} kind="line"
              series={[{ key: 'exposure', label: 'Exposure', color: '#3987e5', values: rows.map((r) => r.risk_exposure) }]} />
          </div>
        ))}
      </main>
    </div>
  )
}
