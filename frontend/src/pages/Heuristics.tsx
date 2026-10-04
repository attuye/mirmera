/** The heuristics library: oracles (how to recognise a problem), product elements
 *  (what to cover), quality criteria (which kinds of good matter) and techniques
 *  (ways to vary testing). Pick heuristics for a charter; they come back as test
 *  ideas in its sessions. */
import { useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Search } from 'lucide-react'
import Sidebar from '../components/Sidebar'
import { ErrorLine, errorText, input, quietButton } from '../components/ui'
import { api, Charter, Heuristic, HeuristicsLibrary } from '../api/client'
import { useProject } from '../project'

const GROUP_COLOR: Record<Heuristic['group'], string> = {
  oracle: 'var(--danger)', element: 'var(--info)', quality: 'var(--warning)', technique: 'var(--accent)',
}

function Card({ h, charters, onAdd, highlighted }: {
  h: Heuristic; charters: Charter[]; onAdd: (charter: Charter, key: string) => Promise<void>; highlighted: boolean
}) {
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const open = charters.filter((c) => c.status !== 'done')
  const already = (c: Charter) => c.heuristics.includes(h.key) || (h.group === 'oracle' && c.oracles.includes(h.key))
  return (
    <article id={h.key} aria-label={h.name} style={{
      background: 'var(--panel)', border: `1px solid ${highlighted ? 'var(--accent)' : 'var(--border)'}`, borderTop: `3px solid ${GROUP_COLOR[h.group]}`,
      borderRadius: 10, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 6, scrollMarginTop: 20,
    }}>
      <h3 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>{h.name}</h3>
      <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', margin: 0, lineHeight: 1.5 }}>{h.summary}</p>
      <ul style={{ margin: '2px 0 0', paddingLeft: 18, fontSize: 12, lineHeight: 1.6 }}>{h.questions.map((q) => <li key={q}>{q}</li>)}</ul>
      <p style={{ fontSize: 11.5, color: 'var(--text-tertiary)', margin: 0, fontStyle: 'italic' }}>e.g. {h.example}</p>
      {open.length > 0 && (
        <div style={{ display: 'flex', gap: 6, marginTop: 'auto', paddingTop: 6 }}>
          <select aria-label={`Charter for ${h.name}`} value={target} onChange={(e) => setTarget(e.target.value)} style={{ ...input, fontSize: 12, padding: '4px 6px' }}>
            <option value="">Use in a charter…</option>
            {open.map((c) => <option key={c.id} value={c.id} disabled={already(c)}>{already(c) ? '✓ ' : ''}{c.mission_statement.slice(0, 70)}</option>)}
          </select>
          <button disabled={!target || busy} style={{ ...quietButton, fontSize: 12, padding: '4px 10px' }} onClick={async () => {
            const c = open.find((x) => x.id === target)
            if (!c) return
            setBusy(true)
            try { await onAdd(c, h.key); setTarget('') } finally { setBusy(false) }
          }}>Add</button>
        </div>
      )}
    </article>
  )
}

export default function Heuristics() {
  const { project } = useProject()
  const { hash } = useLocation()
  const [lib, setLib] = useState<HeuristicsLibrary | null>(null)
  const [charters, setCharters] = useState<Charter[]>([])
  const [query, setQuery] = useState('')
  const [group, setGroup] = useState<'all' | Heuristic['group']>('all')
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const focus = hash.replace('#', '')

  useEffect(() => {
    api.heuristics().then(setLib).catch((e) => setError(errorText(e)))
    api.listCharters(project.id).then(setCharters).catch(() => {})
  }, [project.id])
  useEffect(() => {  // a link to /heuristics#claims scrolls to that card
    if (lib && focus) document.getElementById(focus)?.scrollIntoView({ block: 'center' })
  }, [lib, focus])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (lib?.heuristics ?? []).filter((h) => (group === 'all' || h.group === group) &&
      (!q || [h.name, h.summary, h.example, ...h.questions].join(' ').toLowerCase().includes(q)))
  }, [lib, query, group])

  async function add(c: Charter, key: string) {
    setError(null)
    try {
      const updated = await api.setCharterHeuristics(c.id, [...c.heuristics, key])
      setCharters((prev) => prev.map((x) => (x.id === c.id ? updated : x)))
      setNotice(`Added to "${c.mission_statement.slice(0, 60)}": it comes back as a test idea in that charter's sessions.`)
    } catch (e) { setError(errorText(e)) }
  }

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '32px 40px', maxWidth: 1200 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Heuristics</h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 720, lineHeight: 1.5 }}>
          Fallible ways to find problems, named so you can reach for them on purpose. Add one to a charter and it comes back
          as a test idea in that charter's sessions. FEW HICCUPPS, SFDIPOT and the quality criteria come from James Bach and
          Michael Bolton's Rapid Software Testing; the summaries here are Mirmera's own.
        </p>

        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 18, flexWrap: 'wrap' }}>
          <label style={{ position: 'relative', flex: '0 1 320px' }}>
            <Search size={14} aria-hidden style={{ position: 'absolute', left: 10, top: 10, color: 'var(--text-quiet)' }} />
            <input aria-label="Search heuristics" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search: date, keyboard, slow…"
              style={{ ...input, paddingLeft: 30 }} />
          </label>
          <div role="group" aria-label="Group" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {[{ key: 'all', name: 'All' }, ...(lib?.groups ?? [])].map((g) => (
              <button key={g.key} aria-pressed={group === g.key} onClick={() => setGroup(g.key as typeof group)} style={{
                ...quietButton, fontSize: 12, padding: '5px 10px', ...(group === g.key ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}),
              }}>{g.name}</button>
            ))}
          </div>
        </div>
        <ErrorLine message={error} />
        {notice && <div role="status" style={{ fontSize: 12.5, color: 'var(--accent)', marginTop: 10 }}>{notice}</div>}

        {lib?.groups.filter((g) => shown.some((h) => h.group === g.key)).map((g) => (
          <section key={g.key} aria-label={g.name} style={{ marginTop: 26 }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 2px' }}>
              {g.name} {g.mnemonic && <span className="mono" style={{ fontSize: 11, color: 'var(--text-tertiary)', fontWeight: 400 }}>{g.mnemonic}</span>}
            </h2>
            <p style={{ fontSize: 12.5, color: 'var(--text-tertiary)', margin: '0 0 10px' }}>{g.about}</p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
              {shown.filter((h) => h.group === g.key).map((h) => (
                <Card key={h.key} h={h} charters={charters} onAdd={add} highlighted={focus === h.key} />
              ))}
            </div>
          </section>
        ))}
        {lib && shown.length === 0 && <p style={{ fontSize: 13, color: 'var(--text-tertiary)', marginTop: 20 }}>Nothing matches "{query}".</p>}
      </main>
    </div>
  )
}
