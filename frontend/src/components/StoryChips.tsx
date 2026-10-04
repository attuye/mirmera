/** The user stories a charter or risk belongs to, as links to each story's page. */
import { Link } from 'react-router-dom'
import { StoryRef } from '../api/client'
import { useProjectPath } from '../project'

export default function StoryChips({ stories, label = 'Stories' }: { stories: StoryRef[]; label?: string }) {
  const path = useProjectPath()
  if (!stories.length) return null
  return (
    <span aria-label={label} style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
      {stories.map((s) => (
        <Link key={s.id} to={path(`/stories/${s.id}`)} onClick={(e) => e.stopPropagation()} title={s.title} style={{
          fontSize: 11, color: 'var(--info)', border: '1px solid var(--border)', borderRadius: 5, padding: '1px 7px', maxWidth: 260,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{s.key ?? 'Story'}: {s.title}</Link>
      ))}
    </span>
  )
}
