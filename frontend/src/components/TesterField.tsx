/** "Testing as": who a new session is recorded for, prefilled with the project's default tester. */
import { useEffect, useState } from 'react'
import { Project } from '../api/client'
import { input } from './ui'

export function useTesterName(project: Project): [string, (name: string) => void] {
  const [name, setName] = useState(project.default_tester ?? '')
  useEffect(() => { setName((current) => current || project.default_tester || '') }, [project.default_tester])
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
