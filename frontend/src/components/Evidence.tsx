/** Display pieces for what the browser runner recorded. */
import { Evidence, EvidenceKind, evidenceFileUrl } from '../api/client'

export const SIGNAL_KINDS: EvidenceKind[] = ['console_error', 'page_error', 'failed_request', 'slow_request', 'accessibility']

export const KIND_META: Record<EvidenceKind, { one: string; plural: string; color: string }> = {
  console_error: { one: 'console error', plural: 'console errors', color: 'var(--danger)' },
  page_error: { one: 'page error', plural: 'page errors', color: 'var(--danger)' },
  failed_request: { one: 'failed request', plural: 'failed requests', color: 'var(--warning)' },
  slow_request: { one: 'slow request', plural: 'slow requests', color: 'var(--warning)' },
  accessibility: { one: 'accessibility finding', plural: 'accessibility findings', color: 'var(--question)' },
  navigation: { one: 'page visited', plural: 'pages visited', color: 'var(--info)' },
  action: { one: 'action recorded', plural: 'actions recorded', color: 'var(--question)' },
  screenshot: { one: 'screenshot', plural: 'screenshots', color: 'var(--accent)' },
  trace: { one: 'trace', plural: 'traces', color: 'var(--text-tertiary)' },
  har: { one: 'network log', plural: 'network logs', color: 'var(--text-tertiary)' },
  api_call: { one: 'API request', plural: 'API requests', color: 'var(--info)' },
}

/** Timestamps from the API are naive UTC. */
export const utcDate = (iso: string) => new Date(iso.endsWith('Z') ? iso : iso + 'Z')
export const clock = (iso: string) => utcDate(iso).toLocaleTimeString()

/** Shortens a URL to its path for display; the full URL stays in the tooltip. */
export function shortUrl(url: string | null): string {
  if (!url) return ''
  try {
    const u = new URL(url)
    const text = `${u.host}${u.pathname}${u.search}`
    try { return decodeURIComponent(text) } catch { return text }  // show "?q=…", not "?q=%E2%80%A6"
  } catch {
    return url
  }
}

export function SignalRow({ e }: { e: Evidence }) {
  const meta = KIND_META[e.kind]
  return (
    <li aria-label={e.summary} title={e.url ?? undefined} style={{
      listStyle: 'none', display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 12, padding: '5px 0',
      borderBottom: '1px solid var(--border-soft)',
    }}>
      <span className="mono" style={{ color: 'var(--text-quiet)', fontSize: 10.5, flexShrink: 0 }}>{clock(e.captured_at)}</span>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: meta.color, flexShrink: 0, transform: 'translateY(-1px)' }} />
      <span style={{ color: 'var(--text-secondary)', wordBreak: 'break-word', flex: 1 }}>
        {e.kind === 'navigation' ? shortUrl(e.url) : e.summary}
      </span>
      {e.count > 1 && <span className="mono" style={{ color: 'var(--text-tertiary)', fontSize: 11 }}>×{e.count}</span>}
    </li>
  )
}

export function SignalList({ items, empty }: { items: Evidence[]; empty?: string }) {
  if (!items.length) return empty ? <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{empty}</div> : null
  return <ul style={{ margin: 0, padding: 0 }}>{items.map((e) => <SignalRow key={e.id} e={e} />)}</ul>
}

export function Screenshot({ e, width = 220 }: { e: Evidence; width?: number }) {
  return (
    <figure style={{ margin: 0, width }}>
      <a href={evidenceFileUrl(e.id)} target="_blank" rel="noreferrer" title="Open full size">
        <img src={evidenceFileUrl(e.id)} alt={e.summary} loading="lazy" style={{
          width: '100%', aspectRatio: '16 / 10', objectFit: 'cover', objectPosition: 'top', display: 'block',
          borderRadius: 7, border: '1px solid var(--border)', background: 'var(--panel-alt)',
        }} />
      </a>
      <figcaption style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 4 }}>{e.summary}</figcaption>
    </figure>
  )
}

/** Download links for the trace and HAR, with a pointer to the trace viewer. */
export function RecordingFiles({ items }: { items: Evidence[] }) {
  const files = items.filter((e) => (e.kind === 'trace' || e.kind === 'har') && e.has_file)
  if (!files.length) return null
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', alignItems: 'baseline', fontSize: 12.5 }}>
      {files.map((f) => (
        <a key={f.id} href={evidenceFileUrl(f.id)} download style={{ color: 'var(--accent)' }}>
          Download {f.kind === 'trace' ? 'trace' : 'network log (HAR)'}
        </a>
      ))}
      {files.some((f) => f.kind === 'trace') && (
        <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>
          Open the trace at <a href="https://trace.playwright.dev" target="_blank" rel="noreferrer" style={{ color: 'var(--text-secondary)' }}>trace.playwright.dev</a> to replay the session step by step.
        </span>
      )}
    </div>
  )
}

/** "3 console errors · 2 failed requests · 5 pages visited" */
export function countLine(counts: Partial<Record<EvidenceKind, number>>, kinds: EvidenceKind[]): string {
  return kinds
    .filter((k) => counts[k])
    .map((k) => `${counts[k]} ${counts[k] === 1 ? KIND_META[k].one : KIND_META[k].plural}`)
    .join(' · ')
}
