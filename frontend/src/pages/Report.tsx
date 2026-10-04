/** The project's test report. Summary (default) fits one screen: at a glance,
 *  three small charts, the top bugs. Full adds the three parts of the testing
 *  story, folded, with "How good the testing was" open. Exports follow the view.
 *  Everything comes from what was recorded; the AI writes none of it. */
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, CheckCircle2, ChevronRight, CircleDot, Clock, Download, Printer, ShieldAlert, type LucideIcon,
} from 'lucide-react'
import Sidebar from '../components/Sidebar'
import SprintPicker from '../components/SprintPicker'
import ReportSummaryCard from '../components/ReportSummaryCard'
import { utcDate } from '../components/Evidence'
import { ErrorLine, errorText, fieldLabel, input, quietButton } from '../components/ui'
import {
  api, GlanceLine, ProjectReport, ReportAction, ReportBlock, ReportCell, ReportPart, ReportSection, reportUrl,
} from '../api/client'
import { useProject, useProjectPath } from '../project'

type View = 'summary' | 'full'
const VIEW_KEY = 'mirmera.report.view'
const ROWS = 5  // rows shown before "Show all"
const LONG = 180  // characters shown before "more"
const GAPS = 6  // gaps shown before "Show all"

const isoDay = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => isoDay(new Date(Date.now() - (n - 1) * 86_400_000))
const PRESETS: [string, number | null][] = [['Last 7 days', 7], ['Last 30 days', 30], ['All time', null]]

function hours(seconds: number): string {
  const m = Math.round(seconds / 60)
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min` : `${m} min`
}

const TONE: Record<GlanceLine['tone'], { color: string; icon: LucideIcon }> = {
  danger: { color: 'var(--danger)', icon: AlertTriangle },
  warning: { color: 'var(--warning)', icon: AlertTriangle },
  ok: { color: 'var(--accent)', icon: CheckCircle2 },
  neutral: { color: 'var(--text-secondary)', icon: Clock },
  question: { color: 'var(--question)', icon: CircleDot },
}

const card: React.CSSProperties = { background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, padding: '14px 18px', marginTop: 16 }
const chip = (on: boolean): React.CSSProperties => ({
  ...quietButton, fontSize: 12, padding: '5px 10px', ...(on ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}),
})

function GoLink({ to, label }: { to: string; label: string }) {
  const path = useProjectPath()
  return (
    <Link to={path(to)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--accent)', fontSize: 12, whiteSpace: 'nowrap' }}>
      {label}<ArrowRight size={13} strokeWidth={1.8} aria-hidden />
    </Link>
  )
}

function Glance({ lines }: { lines: GlanceLine[] }) {
  return (
    <section aria-label="At a glance" style={card}>
      <h2 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 4px' }}>At a glance</h2>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {lines.map((g, i) => {
          const tone = TONE[g.tone]
          const Icon = g.to === '/risks' ? ShieldAlert : tone.icon
          return (
            <li key={g.title} aria-label={g.title} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '9px 0', borderTop: i ? '1px solid var(--border-soft)' : 'none' }}>
              <Icon size={16} strokeWidth={1.8} color={tone.color} aria-hidden style={{ flexShrink: 0, marginTop: 1 }} />
              <div style={{ flex: 1, fontSize: 13, lineHeight: 1.45 }}>
                <div style={{ fontWeight: 600 }}>{g.title}</div>
                {g.detail && <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{g.detail}</div>}
              </div>
              {g.to && g.label && <GoLink to={g.to} label={g.label} />}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function Bar({ title, total, parts, legend }: {
  title: string; total: string
  parts: { value: number; color: string; dashed?: boolean }[]
  legend: { label: string; color: string; dashed?: boolean }[]
}) {
  const sum = parts.reduce((n, p) => n + p.value, 0)
  return (
    <figure aria-label={title} style={{ flex: 1, minWidth: 220, margin: 0, border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' }}>
      <figcaption style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11.5, color: 'var(--text-tertiary)', marginBottom: 8 }}>
        <span>{title}</span><span className="mono">{total}</span>
      </figcaption>
      <div style={{ display: 'flex', gap: 2, height: 10, borderRadius: 5, overflow: 'hidden', background: 'var(--panel-alt)' }}>
        {sum > 0 && parts.filter((p) => p.value > 0).map((p, i) => (
          <div key={i} style={{ flex: p.value, background: p.dashed ? 'transparent' : p.color, border: p.dashed ? `1px dashed ${p.color}` : 'none' }} />
        ))}
      </div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8, fontSize: 11, color: 'var(--text-secondary)' }}>
        {legend.map((l) => (
          <span key={l.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <i style={{ width: 8, height: 8, borderRadius: 2, background: l.dashed ? 'transparent' : l.color, border: l.dashed ? `1px dashed ${l.color}` : 'none' }} />
            {l.label}
          </span>
        ))}
      </div>
    </figure>
  )
}

const PHASES: [string, string, string][] = [
  ['on_charter', 'On charter', 'var(--info)'], ['bug_investigation', 'Bugs', 'var(--danger)'],
  ['setup', 'Setup', '#4b5260'], ['opportunity', 'Opportunity', 'var(--question)'],
]

function Charts({ report }: { report: ProjectReport }) {
  const { severity: sev, risks, time } = report.charts
  const accepted = risks.tested + risks.untested
  return (
    <section aria-label="Charts" style={{ display: 'flex', gap: 12, marginTop: 16, flexWrap: 'wrap' }}>
      <Bar title="Bugs by severity" total={String(sev.high + sev.medium + sev.low)}
        parts={[{ value: sev.high, color: 'var(--danger)' }, { value: sev.medium, color: 'var(--warning)' }, { value: sev.low, color: '#4b5260' }]}
        legend={[{ label: `High ${sev.high}`, color: 'var(--danger)' }, { label: `Medium ${sev.medium}`, color: 'var(--warning)' }, { label: `Low ${sev.low}`, color: '#4b5260' }]} />
      <Bar title="Accepted risks tested" total={`${risks.tested} of ${accepted}`}
        parts={[{ value: risks.tested, color: 'var(--accent)' }, { value: risks.untested, color: 'var(--danger)', dashed: true }]}
        legend={[{ label: `Tested ${risks.tested}`, color: 'var(--accent)' },
          { label: `Not tested ${risks.untested}${risks.untested_high ? ` (${risks.untested_high} high)` : ''}`, color: 'var(--danger)', dashed: true }]} />
      <Bar title="Where the time went" total={hours(time.seconds)}
        parts={PHASES.map(([k, , color]) => ({ value: time.split[k] ?? 0, color }))}
        legend={Object.keys(time.split).length
          ? PHASES.filter(([k]) => time.split[k]).map(([k, label, color]) => ({ label: `${label} ${Math.round(time.split[k] * 100)}%`, color }))
          : [{ label: 'Needs debriefed sessions', color: 'var(--border)' }]} />
    </section>
  )
}

function Cell({ cell }: { cell: ReportCell }) {
  if (Array.isArray(cell)) return <>{cell.map((c, i) => <span key={i}>{i > 0 && ', '}<Cell cell={c} /></span>)}</>
  if (typeof cell === 'object') return <a href={cell.href} target="_blank" rel="noreferrer" className="mono" style={{ fontSize: 11.5, color: 'var(--info)' }}>{cell.text} ↗</a>
  if (cell === 'high' || cell === 'medium' || cell === 'low') {
    const c = { high: ['var(--danger)', 'var(--danger-bg)'], medium: ['var(--warning)', 'var(--warning-bg)'], low: ['var(--text-secondary)', 'var(--panel-alt)'] }[cell]
    return <span style={{ fontSize: 11, fontWeight: 600, color: c[0], background: c[1], padding: '2px 7px', borderRadius: 5, textTransform: 'capitalize' }}>{cell}</span>
  }
  return <>{cell}</>
}

function Table({ headers, rows, limit = ROWS }: { headers: string[]; rows: ReportCell[][]; limit?: number }) {
  const [all, setAll] = useState(false)
  const shown = all ? rows : rows.slice(0, limit)
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, margin: '4px 0 6px' }}>
        <thead>
          <tr style={{ color: 'var(--text-tertiary)', textAlign: 'left' }}>
            {headers.map((h) => <th key={h} style={{ fontWeight: 500, padding: '4px 6px' }}>{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i} style={{ borderTop: '1px solid var(--border-soft)' }}>
              {row.map((c, j) => <td key={j} style={{ padding: '7px 6px', verticalAlign: 'top' }}><Cell cell={c} /></td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > limit && (
        <button onClick={() => setAll(!all)} style={{ background: 'none', padding: 0, fontSize: 12, color: 'var(--text-secondary)' }}>
          {all ? 'Show fewer' : `Show all ${rows.length}`}
        </button>
      )}
    </div>
  )
}

function LongText({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  if (text.length <= LONG) return <>{text}</>
  return (
    <>
      {open ? text : `${text.slice(0, LONG).trimEnd()}…`}{' '}
      <button onClick={() => setOpen(!open)} style={{ background: 'none', padding: 0, fontSize: 11.5, color: 'var(--text-tertiary)' }}>
        {open ? 'less' : 'more'}
      </button>
    </>
  )
}

function Actions({ items }: { items: ReportAction[] }) {
  const [all, setAll] = useState(false)
  const shown = all ? items : items.slice(0, GAPS)
  return (
    <>
    <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {shown.map((a) => (
        <li key={a.text} style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '6px 0', fontSize: 12.5 }}>
          <AlertTriangle size={14} strokeWidth={1.8} color="var(--warning)" aria-hidden style={{ flexShrink: 0, alignSelf: 'center' }} />
          <span style={{ flex: 1 }}>{a.text}</span>
          <GoLink to={a.to} label={a.label} />
        </li>
      ))}
    </ul>
    {items.length > GAPS && (
      <button onClick={() => setAll(!all)} style={{ background: 'none', padding: 0, fontSize: 12, color: 'var(--text-secondary)' }}>
        {all ? 'Show fewer' : `Show all ${items.length}`}
      </button>
    )}
    </>
  )
}

function Block({ block }: { block: ReportBlock }) {
  switch (block.type) {
    case 'heading':
      return <h3 style={{ fontSize: 13.5, fontWeight: 600, margin: '14px 0 6px', color: 'var(--text-secondary)' }}>{block.text}</h3>
    case 'paragraph':
      return <p style={{ fontSize: 13, lineHeight: 1.6, margin: '6px 0' }}>{block.text}</p>
    case 'bullets':
      return <ul style={{ margin: '4px 0', paddingLeft: 20, fontSize: 13, lineHeight: 1.6 }}>{block.items.map((i, n) => <li key={n}><LongText text={i} /></li>)}</ul>
    case 'actions':
      return <Actions items={block.items} />
    case 'quote':
      return (
        <blockquote style={{ margin: '8px 0', padding: '4px 12px', borderLeft: '3px solid var(--border)', fontSize: 13 }}>
          {block.text}<div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 2 }}>— {block.by}</div>
        </blockquote>
      )
    case 'table':
      return <Table headers={block.headers} rows={block.rows} />
  }
}

function Part({ part }: { part: ReportPart }) {
  const [open, setOpen] = useState(part.open)
  if (!part.title) return <>{part.blocks.map((b, i) => <Block key={i} block={b} />)}</>
  return (
    <div style={{ borderTop: '1px solid var(--border-soft)' }}>
      <button onClick={() => setOpen(!open)} aria-expanded={open} style={{
        display: 'flex', alignItems: 'center', gap: 8, width: '100%', background: 'none', border: 'none', padding: '10px 0 6px',
        fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', textAlign: 'left', cursor: 'pointer', fontFamily: 'var(--font-ui)',
      }}>
        <ChevronRight size={14} aria-hidden style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 120ms' }} />
        {part.title}
        {part.count !== null && <span style={{ fontWeight: 400, color: 'var(--text-tertiary)' }}>{part.count}</span>}
      </button>
      {open && <div style={{ paddingBottom: 6 }}>{part.blocks.map((b, i) => <Block key={i} block={b} />)}</div>}
    </div>
  )
}

function Section({ section }: { section: ReportSection }) {
  const [open, setOpen] = useState(section.open)
  return (
    <section aria-label={section.title} style={{ border: '1px solid var(--border)', borderRadius: 12, marginTop: 12, overflow: 'hidden' }}>
      <h2 style={{ margin: 0 }}>
        <button onClick={() => setOpen(!open)} aria-expanded={open} style={{
          display: 'flex', alignItems: 'center', gap: 10, width: '100%', background: 'none', border: 'none', padding: '13px 16px',
          fontSize: 14.5, fontWeight: 600, color: 'var(--text)', textAlign: 'left', cursor: 'pointer', fontFamily: 'var(--font-ui)',
        }}>
          <ChevronRight size={16} color="var(--text-secondary)" aria-hidden style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 120ms' }} />
          <span style={{ flex: 1 }}>{section.title}</span>
          <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--text-tertiary)' }}>{section.counts}</span>
        </button>
      </h2>
      {open && <div style={{ padding: '0 16px 12px' }}>{section.parts.map((p, i) => <Part key={i} part={p} />)}</div>}
    </section>
  )
}

function Tile({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 14px', flex: 1, minWidth: 130 }}>
      <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, marginTop: 2 }}>{value}</div>
      {note && <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{note}</div>}
    </div>
  )
}

function loadView(): View {
  try { return localStorage.getItem(VIEW_KEY) === 'full' ? 'full' : 'summary' } catch { return 'summary' }
}

export default function ReportPage() {
  const { project } = useProject()
  const [params] = useSearchParams()  // a sprint's page links here with its dates
  const [since, setSince] = useState(params.get('since') ?? '')
  const [until, setUntil] = useState(params.get('until') ?? '')
  const [custom, setCustom] = useState(false)
  const [view, setViewState] = useState<View>(loadView)
  const [report, setReport] = useState<ProjectReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  function setView(v: View) {
    setViewState(v)
    try { localStorage.setItem(VIEW_KEY, v) } catch { /* not remembered */ }
  }

  const load = () => api.getReport(project.id, since || undefined, until || undefined).then(setReport).catch((e) => setError(errorText(e)))
  useEffect(() => { setError(null); load() }, [project.id, since, until])  // eslint-disable-line react-hooks/exhaustive-deps

  const preset = (days: number | null) => {
    setCustom(false)
    setSince(days === null ? '' : daysAgo(days)); setUntil(days === null ? '' : isoDay(new Date()))
  }
  const activePreset = custom ? null : PRESETS.find(([, d]) => (d === null ? !since && !until : since === daysAgo(d) && until === isoDay(new Date())))?.[0]
  const s = report?.summary
  const tb = report?.top_bugs

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1100 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Test report</h1>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 600, lineHeight: 1.5 }}>
              The testing story of {project.name}, built from what was recorded.
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div role="group" aria-label="View" style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
              {(['summary', 'full'] as View[]).map((v) => (
                <button key={v} onClick={() => setView(v)} aria-pressed={view === v} style={{
                  border: 'none', padding: '7px 14px', fontSize: 12.5, cursor: 'pointer', fontFamily: 'var(--font-ui)',
                  background: view === v ? 'var(--accent-bg)' : 'transparent', color: view === v ? 'var(--accent)' : 'var(--text-secondary)',
                  fontWeight: view === v ? 600 : 400,
                }}>{v === 'summary' ? 'Summary' : 'Full'}</button>
              ))}
            </div>
            <a href={reportUrl(project.id, 'md', since || undefined, until || undefined, view)} download aria-label="Download Markdown"
              style={{ ...quietButton, display: 'inline-flex', alignItems: 'center', gap: 6 }}><Download size={14} aria-hidden />Markdown</a>
            <a href={reportUrl(project.id, 'html', since || undefined, until || undefined, view)} target="_blank" rel="noreferrer" aria-label="Printable page"
              style={{ ...quietButton, display: 'inline-flex', alignItems: 'center', gap: 6 }}><Printer size={14} aria-hidden />Print</a>
          </div>
        </div>

        <div role="group" aria-label="Period" style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 16, flexWrap: 'wrap' }}>
          {PRESETS.map(([label, days]) => (
            <button key={label} onClick={() => preset(days)} aria-pressed={activePreset === label} style={chip(activePreset === label)}>{label}</button>
          ))}
          <SprintPicker projectId={project.id} since={since} until={until} onPick={(sp) => { setCustom(false); setSince(sp.starts_on); setUntil(sp.ends_on) }} />
          <button onClick={() => setCustom(true)} aria-pressed={custom} style={chip(custom)}>Custom…</button>
          {custom && (
            <>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ ...fieldLabel, margin: 0 }}>From</span>
                <input type="date" value={since} max={until || undefined} onChange={(e) => setSince(e.target.value)} style={{ ...input, width: 150, padding: '5px 8px' }} /></label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ ...fieldLabel, margin: 0 }}>To</span>
                <input type="date" value={until} min={since || undefined} onChange={(e) => setUntil(e.target.value)} style={{ ...input, width: 150, padding: '5px 8px' }} /></label>
            </>
          )}
          {report && (
            <span role="status" aria-label="Report period" className="mono" style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginLeft: 4 }}>
              {report.period} · generated {utcDate(report.generated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          )}
        </div>
        <ErrorLine message={error} />

        {report && s && tb && (
          <>
            <section aria-label="Totals" style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 14 }}>
              <Tile label="Sessions" value={s.sessions} note={`${hours(s.tested_seconds)} · ${s.testers} tester${s.testers === 1 ? '' : 's'}`} />
              <Tile label="Bugs" value={s.bugs} note={`${s.high} high · ${s.medium} medium · ${s.low} low`} />
              <Tile label="Questions" value={s.questions} note={s.unreviewed ? `${s.unreviewed} to review` : undefined} />
              <Tile label="Filed" value={s.filed} note={`${s.closed} closed`} />
              <Tile label="Regression tests" value={s.regression_tests} note={s.fix_verified || s.still_fails ? `${s.fix_verified} fix verified · ${s.still_fails} still fail` : undefined} />
            </section>

            <ReportSummaryCard projectId={project.id} since={since || undefined} until={until || undefined}
              kept={report.ai_summary} onChange={load} />
            <Glance lines={report.glance} />

            {view === 'summary' ? (
              <>
                <Charts report={report} />
                {tb.rows.length > 0 && (
                  <section aria-label="Top bugs" style={card}>
                    <h2 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 4px' }}>
                      Top bugs <span style={{ fontWeight: 400, color: 'var(--text-tertiary)', fontSize: 12 }}>high first</span>
                    </h2>
                    <Table headers={tb.headers} rows={tb.rows} />
                    {tb.total > tb.rows.length && (
                      <button onClick={() => setView('full')} style={{ background: 'none', padding: 0, fontSize: 12, color: 'var(--text-secondary)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        Show all {tb.bugs} bug{tb.bugs === 1 ? '' : 's'} and {tb.questions} question{tb.questions === 1 ? '' : 's'} in Full <ArrowRight size={13} aria-hidden />
                      </button>
                    )}
                  </section>
                )}
                <p style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 14 }}>
                  Full adds the sessions, every risk, coverage notes, obstacles, open questions and how the testers felt.
                </p>
              </>
            ) : (
              <article aria-label="Report">{report.sections.map((sec) => <Section key={sec.key} section={sec} />)}</article>
            )}
          </>
        )}
      </main>
    </div>
  )
}
