/** "Testing as": who a new session is recorded for, prefilled with the project's default tester,
 *  or else the signed-in person's name. */
import { useEffect, useState } from 'react'
import { Project } from '../api/client'
import { useAuth } from '../auth'
import { input } from './ui'

export function useTesterName(project: Project): [string, (name: string) => void] {
  const { me } = useAuth()
  const fallback = project.default_tester || me?.user.name || ''
  const [name, setName] = useState(fallback)
  useEffect(() => { setName((current) => current || fallback) }, [fallback])
  return [name, setName]
}

export default function TesterField({ value, onChange }: { value: string; onChange: (name: string) => void }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-tertiary)' }}>
      Testing as
      <input aria-label="Tester name" value={value} onChange={(e) => onChange(e.target.value)} placeholder="Your name"
        maxLength={60} style={{ ...input, width: 150, padding: '6px 9px', fontSize: 12.5 }} />
    </label>
  )
}
