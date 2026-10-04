/** The project's test report: the product, the testing, and how good the testing
 *  was, for a period. Built from what was recorded; download it as Markdown or
 *  open a printable page (print to PDF from the browser). */
import { useEffect, useState } from 'react'
import Sidebar from '../components/Sidebar'
import { utcDate } from '../components/Evidence'
import { ErrorLine, errorText, fieldLabel, input, quietButton } from '../components/ui'
import { api, ProjectReport, ReportBlock, ReportCell, reportUrl } from '../api/client'
import { useProject } from '../project'

const isoDay = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => isoDay(new Date(Date.now() - n * 86_400_000))

function hours(seconds: number): string {
  const m = Math.round(seconds / 60)
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min` : `${m} min`
}

function Cell({ cell }: { cell: ReportCell }) {
  if (Array.isArray(cell)) return <>{cell.map((c, i) => <span key={i}>{i > 0 && ', '}<Cell cell={c} /></span>)}</>
  if (typeof cell === 'object') return <a href={cell.href} target="_blank" rel="noreferrer" className="mono" style={{ fontSize: 11.5 }}>{cell.text} ↗</a>
  return <>{cell}</>
}

function Block({ block }: { block: ReportBlock }) {
  switch (block.type) {
    case 'heading':
      return block.level === 2
        ? <h2 style={{ fontSize: 16, fontWeight: 600, margin: '28px 0 8px', paddingBottom: 6, borderBottom: '1px solid var(--border)' }}>{block.text}</h2>
        : <h3 style={{ fontSize: 13.5, fontWeight: 600, margin: '18px 0 6px', color: 'var(--text-secondary)' }}>{block.text}</h3>
    case 'paragraph':
      return <p style={{ fontSize: 13, lineHeight: 1.6, margin: '6px 0', color: 'var(--text)' }}>{block.text}</p>
    case 'bullets':
      return <ul style={{ margin: '4px 0', paddingLeft: 20, fontSize: 13, lineHeight: 1.6 }}>{block.items.map((i, n) => <li key={n}>{i}</li>)}</ul>
    case 'quote':
      return (
        <blockquote style={{ margin: '8px 0', padding: '4px 12px', borderLeft: '3px solid var(--border)', fontSize: 13 }}>
          {block.text}
          <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 2 }}>— {block.by}</div>
        </blockquote>
      )
    case 'table':
      return (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, margin: '6px 0 10px' }}>
            <thead>
              <tr style={{ color: 'var(--text-tertiary)', textAlign: 'left' }}>
                {block.headers.map((h) => <th key={h} style={{ fontWeight: 500, padding: '4px 6px' }}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, i) => (
                <tr key={i} style={{ borderTop: '1px solid var(--border-soft)' }}>
                  {row.map((c, j) => <td key={j} style={{ padding: '6px', verticalAlign: 'top' }}><Cell cell={c} /></td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
  }
}

function Tile({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 14px', minWidth: 120 }}>
      <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, marginTop: 2 }}>{value}</div>
      {note && <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{note}</div>}
    </div>
  )
}

export default function ReportPage() {
  const { project } = useProject()
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [report, setReport] = useState<ProjectReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setError(null)
    api.getReport(project.id, since || undefined, until || undefined).then(setReport).catch((e) => setError(errorText(e)))
  }, [project.id, since, until])

  function preset(days: number | null) {
    setSince(days === null ? '' : daysAgo(days - 1)); setUntil(days === null ? '' : isoDay(new Date()))
  }

  const s = report?.summary
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1100 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Test report</h1>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 640, lineHeight: 1.5 }}>
              The testing story of {project.name}: what was found, how it was tested, and how good that testing was.
              Built from the sessions, debriefs and bugs, so it claims nothing that wasn't recorded.
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <a href={reportUrl(project.id, 'md', since || undefined, until || undefined)} download style={{ ...quietButton, display: 'inline-block' }}>Download Markdown</a>
            <a href={reportUrl(project.id, 'html', since || undefined, until || undefined)} target="_blank" rel="noreferrer" style={{ ...quietButton, display: 'inline-block' }}>Printable page</a>
          </div>
        </div>

        <form aria-label="Period" onSubmit={(e) => e.preventDefault()} style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginTop: 20, flexWrap: 'wrap' }}>
          <label><span style={fieldLabel}>From</span><input type="date" value={since} max={until || undefined} onChange={(e) => setSince(e.target.value)} style={{ ...input, width: 160 }} /></label>
          <label><span style={fieldLabel}>To</span><input type="date" value={until} min={since || undefined} onChange={(e) => setUntil(e.target.value)} style={{ ...input, width: 160 }} /></label>
          {([['Last 7 days', 7], ['Last 30 days', 30], ['All time', null]] as const).map(([label, days]) => (
            <button key={label} type="button" onClick={() => preset(days)} style={quietButton}>{label}</button>
          ))}
        </form>
        <ErrorLine message={error} />

        {report && s && (
          <>
            <div role="status" aria-label="Report period" style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 14 }}>
              Period: {report.period} · generated {utcDate(report.generated_at).toLocaleString()}
            </div>
            <section aria-label="Summary" style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 12 }}>
              <Tile label="Sessions" value={s.sessions} note={`${hours(s.tested_seconds)} · ${s.testers} tester${s.testers === 1 ? '' : 's'}`} />
              <Tile label="Bugs" value={s.bugs} note={`${s.high} high · ${s.medium} medium · ${s.low} low`} />
              <Tile label="Questions" value={s.questions} note={s.unreviewed ? `${s.unreviewed} to review` : undefined} />
              <Tile label="Filed" value={s.filed} note={`${s.closed} closed`} />
              <Tile label="Regression tests" value={s.regression_tests} note={s.fix_verified || s.still_fails ? `${s.fix_verified} fix verified · ${s.still_fails} still fail` : undefined} />
            </section>
            <article aria-label="Report" style={{ marginTop: 8 }}>
              {report.blocks.map((b, i) => <Block key={i} block={b} />)}
            </article>
          </>
        )}
      </main>
    </div>
  )
}
