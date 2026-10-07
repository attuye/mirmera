/** Coverage map: which pages and product areas the testing reached, how deeply,
 *  and where to look next. Built from what the recorded browser (or the
 *  extension) saw, and from the charters' risks. */
import { Fragment, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import SprintPicker from '../components/SprintPicker'
import { utcDate } from '../components/Evidence'
import { ErrorLine, errorText, quietButton } from '../components/ui'
import { api, AppMap, CoverageArea, CoverageFlag, CoverageMap, CoveragePage } from '../api/client'
import { elapsed, useAiJob } from '../components/useAiJob'
import { useProject, useProjectPath } from '../project'

const FLAG: Record<CoverageFlag, { label: string; fg: string; bg: string; hint: string }> = {
  not_tried: { label: 'Not tried', fg: 'var(--warning)', bg: 'var(--warning-bg)', hint: 'Opened, but nothing was clicked or typed there' },
  problems_without_bug: { label: 'Problems, no bug', fg: 'var(--danger)', bg: 'var(--danger-bg)', hint: 'Errors were recorded here but no bug was logged' },
  stale: { label: 'Stale', fg: 'var(--text-secondary)', bg: 'var(--panel-alt)', hint: 'Not visited for a while' },
  one_tester: { label: 'One tester', fg: 'var(--text-secondary)', bg: 'var(--panel-alt)', hint: 'Only one person has tested here' },
}
const FILTERS: { key: 'all' | CoverageFlag; label: string }[] = [
  { key: 'all', label: 'All pages' }, { key: 'not_tried', label: 'Not tried' },
  { key: 'problems_without_bug', label: 'Problems, no bug' }, { key: 'stale', label: 'Stale' },
]
const PERIODS: [string, number | null][] = [['All time', null], ['Last 30 days', 30], ['Last 7 days', 7]]

const daysAgo = (n: number) => new Date(Date.now() - (n - 1) * 86_400_000).toISOString().slice(0, 10)
const day = (iso: string | null) => (iso ? utcDate(iso).toLocaleDateString() : '')

function Heat({ value, max }: { value: number; max: number }) {
  return (
    <div title={`${value} session${value === 1 ? '' : 's'}`} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 70, height: 6, borderRadius: 3, background: 'var(--panel-alt)', overflow: 'hidden' }}>
        <div style={{ width: `${max ? (value / max) * 100 : 0}%`, height: '100%', background: 'var(--accent)', opacity: 0.35 + 0.65 * (max ? value / max : 0) }} />
      </div>
      <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{value}</span>
    </div>
  )
}

/** The path with its parent dimmed, indented by depth: /verify/:id reads as a child of /verify. */
function PathLabel({ path }: { path: string }) {
  const parts = path.split('/').filter(Boolean)
  const last = parts.pop()
  return (
    <span className="mono" style={{ fontSize: 12, paddingLeft: parts.length * 14 }}>
      <span style={{ color: 'var(--text-quiet)' }}>{parts.length ? `/${parts.join('/')}` : ''}</span>/{last ?? ''}
    </span>
  )
}

function Details({ page }: { page: CoveragePage }) {
  const path = useProjectPath()
  const list = (title: string, items: React.ReactNode[], empty: string) => (
    <div style={{ minWidth: 220, flex: 1 }}>
      <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 4 }}>{title}</div>
      {items.length ? <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12, lineHeight: 1.6 }}>{items.map((i, n) => <li key={n}>{i}</li>)}</ul>
        : <div style={{ fontSize: 12, color: 'var(--text-quiet)' }}>{empty}</div>}
    </div>
  )
  return (
    <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', padding: '8px 6px 14px' }}>
      {list('Controls used', page.controls.map((c) => <span className="mono" style={{ fontSize: 11.5 }}>{c}</span>), 'Nothing was clicked or typed here.')}
      {list('Problems recorded', page.problems.map((p) => <>{p.summary}{p.count > 1 ? ` ×${p.count}` : ''}</>), 'None.')}
      {list('Bugs logged here', page.bugs.map((b) => <Link to={path('/bugs')}>{b.title}</Link>), 'None.')}
      {list('Tested by', [
        page.testers.join(', '),
        ...page.charters.map((c) => <span style={{ color: 'var(--text-secondary)' }}>{c}</span>),
        <span className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)' }}>{page.hosts.join(', ')}</span>,
      ], '')}
    </div>
  )
}

function Areas({ areas }: { areas: CoverageArea[] }) {
  const groups = useMemo(() => {
    const g: Record<string, CoverageArea[]> = {}
    for (const a of areas) (g[a.category] ??= []).push(a)
    return Object.entries(g)
  }, [areas])
  return (
    <section aria-label="Product areas" style={{ marginTop: 30 }}>
      <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 4px' }}>Product areas</h2>
      <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', margin: '0 0 10px' }}>
        Elements of the product model, covered when a session's charter targets a risk that names them.
      </p>
      {groups.map(([category, items]) => (
        <div key={category} style={{ marginBottom: 12 }}>
          <div className="mono" style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-quiet)', marginBottom: 6 }}>{category}</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {items.map((a) => (
              <div key={a.name} role="listitem" aria-label={a.name}
                title={[a.description, a.risks.length ? `Risks: ${a.risks.join('; ')}` : 'No risk names this area'].filter(Boolean).join('\n')}
                style={{
                  fontSize: 12, padding: '5px 10px', borderRadius: 7,
                  border: a.sessions ? '1px solid var(--border)' : '1px dashed var(--danger)',
                  background: a.sessions ? 'var(--accent-bg)' : 'transparent',
                  color: a.sessions ? 'var(--text)' : 'var(--danger)',
                }}>
                {a.name}{' '}
                <span className="mono" style={{ fontSize: 11, color: a.sessions ? 'var(--accent)' : 'var(--danger)' }}>
                  {a.sessions ? `${a.sessions} session${a.sessions === 1 ? '' : 's'}` : 'not explored'}
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}

/** The app's map, from a survey that opens every page it can reach and changes nothing:
 *  what's there to test, and which pages no session has opened yet. */
function AppMapSection({ projectId, tested }: { projectId: string; tested: Set<string> }) {
  const job = useAiJob<AppMap>(projectId, 'app_map')
  const result = job.job?.status === 'done' ? job.job.result : null
  const untested = result ? result.pages.filter((p) => !tested.has(p.path)).length : 0
  return (
    <section aria-label="App map" style={{ marginTop: 20, border: '1px solid var(--border)', borderRadius: 10, padding: '12px 16px' }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <h2 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>App map</h2>
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)', flex: 1 }}>
          {result ? `${result.pages.length} page${result.pages.length === 1 ? '' : 's'}, ${result.forms} with a form · ${untested} never opened in a session · surveyed ${elapsed(job.job!.finished_at ?? job.job!.created_at)} ago`
            : 'Mirmera opens every page it can reach from the app\'s address and reads its forms. It never submits anything.'}
        </span>
        <button onClick={() => job.start({})} disabled={job.running} style={{ ...quietButton, fontSize: 12, padding: '4px 10px' }}>
          {job.running ? `Surveying… ${job.since}` : result ? 'Survey again' : 'Survey the app'}
        </button>
      </div>
      <ErrorLine message={job.error} />
      {result && (
        <ul aria-label="Pages in the app" style={{ listStyle: 'none', margin: '10px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {result.pages.map((p) => (
            <li key={p.path} aria-label={p.path} style={{ fontSize: 12.5, display: 'flex', gap: 10, alignItems: 'baseline' }}>
              <span className="mono" style={{ minWidth: 120 }}>{p.path}</span>
              <span style={{ flex: 1, color: 'var(--text-secondary)' }}>
                {p.heading || p.title || 'untitled'}
                {p.fields.length > 0 && <span style={{ color: 'var(--text-tertiary)' }}> · {p.fields.map((f) => f.label).join(', ')}</span>}
                {p.error && <span style={{ color: 'var(--warning)' }}> · couldn't open: {p.error}</span>}
              </span>
              <span style={{ fontSize: 11.5, color: tested.has(p.path) ? 'var(--accent)' : 'var(--warning)' }}>{tested.has(p.path) ? 'tested' : 'never tested'}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default function Coverage() {
  const { project } = useProject()
  const path = useProjectPath()
  const [params] = useSearchParams()  // a sprint's page links here with its dates
  const [days, setDaysState] = useState<number | null>(null)
  const [range, setRange] = useState<{ since: string; until: string } | null>(
    params.get('since') && params.get('until') ? { since: params.get('since')!, until: params.get('until')! } : null)
  const setDays = (n: number | null) => { setRange(null); setDaysState(n) }
  const [map, setMap] = useState<CoverageMap | null>(null)
  const [filter, setFilter] = useState<'all' | CoverageFlag>('all')
  const [open, setOpen] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setError(null)
    const load = range ? api.getCoverage(project.id, range.since, range.until) : api.getCoverage(project.id, days ? daysAgo(days) : undefined)
    load.then(setMap).catch((e) => setError(errorText(e)))
  }, [project.id, days, range])

  const pages = map ? map.pages.filter((p) => filter === 'all' || p.flags.includes(filter)) : []
  const max = map ? Math.max(0, ...map.pages.map((p) => p.sessions)) : 0

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1100 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Coverage map</h1>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 640, lineHeight: 1.5 }}>
              Where the testing of {project.name} actually went: every page the recorded browser opened, what was tried
              there, what went wrong, and the product areas no session has reached yet.
            </p>
          </div>
          <div role="group" aria-label="Period" style={{ display: 'flex', gap: 6 }}>
            {PERIODS.map(([label, n]) => (
              <button key={label} onClick={() => setDays(n)} aria-pressed={!range && days === n}
                style={{ ...quietButton, ...(!range && days === n ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}) }}>{label}</button>
            ))}
            <SprintPicker projectId={project.id} since={range?.since ?? ''} until={range?.until ?? ''}
              onPick={(sp) => setRange({ since: sp.starts_on, until: sp.ends_on })} />
          </div>
        </div>
        <ErrorLine message={error} />

        <AppMapSection projectId={project.id} tested={new Set((map?.pages ?? []).map((p) => p.path))} />

        {map && (
          <>
            <div role="status" aria-label="Coverage summary" style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 16 }}>
              {map.pages.length} page{map.pages.length === 1 ? '' : 's'} from {map.recorded_sessions} recorded session
              {map.recorded_sessions === 1 ? '' : 's'} (of {map.sessions})
              {map.areas.length > 0 && ` · ${map.areas.filter((a) => a.sessions).length} of ${map.areas.length} product areas explored`}
            </div>

            {map.gaps.length > 0 && (
              <section aria-label="Where to look next" style={{ marginTop: 16, border: '1px solid var(--border)', borderRadius: 10, padding: '12px 16px' }}>
                <h2 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 6px' }}>Where to look next</h2>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7 }}>
                  {map.gaps.slice(0, 12).map((g) => <li key={g}>{g}</li>)}
                </ul>
                {map.gaps.length > 12 && <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>and {map.gaps.length - 12} more below</div>}
              </section>
            )}

            <section aria-label="Pages" style={{ marginTop: 24 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
                <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Pages</h2>
                <div role="group" aria-label="Show" style={{ display: 'flex', gap: 6 }}>
                  {FILTERS.map((f) => {
                    const n = f.key === 'all' ? map.pages.length : map.pages.filter((p) => p.flags.includes(f.key as CoverageFlag)).length
                    return (
                      <button key={f.key} onClick={() => setFilter(f.key)} aria-pressed={filter === f.key}
                        style={{ ...quietButton, fontSize: 12, padding: '4px 10px', ...(filter === f.key ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}) }}>
                        {f.label} {n}
                      </button>
                    )
                  })}
                </div>
              </div>
              {map.recorded_sessions === 0 ? (
                <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                  No recorded sessions yet. Open the recorded browser in a session (or record a tab with the Chrome extension)
                  and the pages you test will appear here. Start one from the <Link to={path('/dashboard')}>Dashboard</Link>.
                </p>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, marginTop: 8 }}>
                  <thead>
                    <tr style={{ color: 'var(--text-tertiary)', textAlign: 'left' }}>
                      {['Page', 'Sessions', 'Actions', 'Controls', 'Problems', 'Bugs', 'Last visited', ''].map((h) => <th key={h} style={{ fontWeight: 500, padding: '4px 6px' }}>{h}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {pages.map((p) => (
                      <Fragment key={p.path}>
                        <tr aria-label={p.path} onClick={() => setOpen(open === p.path ? null : p.path)}
                          style={{ borderTop: '1px solid var(--border-soft)', cursor: 'pointer', background: open === p.path ? 'var(--panel-alt)' : undefined }}>
                          <td style={{ padding: '7px 6px' }}>
                            <button aria-expanded={open === p.path} aria-label={`Details for ${p.path}`}
                              style={{ background: 'none', border: 'none', padding: 0, color: 'inherit', cursor: 'pointer' }}>
                              <PathLabel path={p.path} />
                            </button>
                          </td>
                          <td style={{ padding: '7px 6px' }}><Heat value={p.sessions} max={max} /></td>
                          <td className="mono" style={{ padding: '7px 6px', fontSize: 11.5 }}>{p.actions}</td>
                          <td className="mono" style={{ padding: '7px 6px', fontSize: 11.5 }}>{p.controls.length}</td>
                          <td className="mono" style={{ padding: '7px 6px', fontSize: 11.5, color: p.problems.length ? 'var(--danger)' : undefined }}>
                            {p.problems.reduce((n, x) => n + x.count, 0)}
                          </td>
                          <td className="mono" style={{ padding: '7px 6px', fontSize: 11.5 }}>{p.bugs.length}</td>
                          <td className="mono" style={{ padding: '7px 6px', fontSize: 11, color: 'var(--text-tertiary)' }}>{day(p.last_seen)}</td>
                          <td style={{ padding: '7px 6px' }}>
                            <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
                              {p.flags.map((f) => (
                                <span key={f} title={FLAG[f].hint} style={{ fontSize: 10.5, fontWeight: 600, color: FLAG[f].fg, background: FLAG[f].bg, padding: '1px 6px', borderRadius: 4, whiteSpace: 'nowrap' }}>{FLAG[f].label}</span>
                              ))}
                            </span>
                          </td>
                        </tr>
                        {open === p.path && (
                          <tr><td colSpan={8} style={{ background: 'var(--panel-alt)' }}><Details page={p} /></td></tr>
                        )}
                      </Fragment>
                    ))}
                    {pages.length === 0 && (
                      <tr><td colSpan={8} style={{ padding: '10px 6px', color: 'var(--text-tertiary)' }}>No pages match.</td></tr>
                    )}
                  </tbody>
                </table>
              )}
            </section>

            {map.areas.length > 0 ? <Areas areas={map.areas} /> : (
              <p style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 26 }}>
                Build a product model on the <Link to={path('/risks')}>Risks</Link> page to see which product areas the sessions reached.
              </p>
            )}
          </>
        )}
      </main>
    </div>
  )
}
