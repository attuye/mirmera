/** Pick a sprint to set a period to its dates (Report, Coverage). Shows nothing when the project has no sprints. */
import { useEffect, useState } from 'react'
import { api, SprintSummary } from '../api/client'
import { input } from './ui'

export default function SprintPicker({ projectId, since, until, onPick }: {
  projectId: string; since: string; until: string; onPick: (s: SprintSummary) => void
}) {
  const [sprints, setSprints] = useState<SprintSummary[]>([])
  useEffect(() => { api.listSprints(projectId).then(setSprints).catch(() => {}) }, [projectId])
  if (!sprints.length) return null
  const chosen = sprints.find((s) => s.starts_on === since && s.ends_on === until)
  return (
    <select aria-label="Sprint" value={chosen?.id ?? ''} onChange={(e) => { const s = sprints.find((x) => x.id === e.target.value); if (s) onPick(s) }}
      style={{ ...input, width: 'auto', padding: '5px 8px', fontSize: 12.5, borderColor: chosen ? 'var(--accent)' : undefined, color: chosen ? 'var(--accent)' : undefined }}>
      <option value="" disabled>Sprint…</option>
      {sprints.map((s) => <option key={s.id} value={s.id}>{s.name}{s.state === 'current' ? ' (current)' : ''}</option>)}
    </select>
  )
}
