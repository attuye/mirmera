/** A charter's heuristics and oracles as small chips linking to the library. */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, Heuristic } from '../api/client'
import { useProjectPath } from '../project'

let cache: Promise<Record<string, Heuristic>> | null = null
export function useHeuristicNames(): Record<string, Heuristic> {
  const [names, setNames] = useState<Record<string, Heuristic>>({})
  useEffect(() => {
    cache ??= api.heuristics().then((lib) => Object.fromEntries(lib.heuristics.map((h) => [h.key, h])))
    cache.then(setNames).catch(() => { cache = null })
  }, [])
  return names
}

export default function HeuristicChips({ heuristics, oracles = [], label = true }: { heuristics: string[]; oracles?: string[]; label?: boolean }) {
  const names = useHeuristicNames()
  const path = useProjectPath()
  if (!heuristics.length && !oracles.length) return null
  const chip = (key: string, color: string) => (
    <Link key={key} to={path(`/heuristics#${key}`)} onClick={(e) => e.stopPropagation()} title={names[key]?.summary}
      style={{ fontSize: 11, color, border: '1px solid var(--border)', borderRadius: 5, padding: '1px 7px', whiteSpace: 'nowrap' }}>
      {names[key]?.name ?? key.replace(/_/g, ' ')}
    </Link>
  )
  return (
    <div aria-label="Heuristics for this charter" style={{ display: 'flex', gap: 5, flexWrap: 'wrap', alignItems: 'center', marginTop: 4 }}>
      {label && heuristics.length > 0 && <span style={{ fontSize: 11, color: 'var(--text-quiet)' }}>Try</span>}
      {heuristics.map((k) => chip(k, 'var(--accent)'))}
      {label && oracles.length > 0 && <span style={{ fontSize: 11, color: 'var(--text-quiet)', marginLeft: heuristics.length ? 6 : 0 }}>Watch for</span>}
      {oracles.map((k) => chip(k, 'var(--danger)'))}
    </div>
  )
}
