/** Mirmera activity: what its agents are doing in this workspace, live, and what they did last. */
import { useParams } from 'react-router-dom'
import { Bot } from 'lucide-react'
import Sidebar from '../components/Sidebar'
import AgentProgress, { useAgentActivity } from '../components/AgentProgress'
import { api } from '../api/client'
import { useAuth } from '../auth'

export default function ActivityPage() {
  const { workspaceId = '' } = useParams()
  const { me } = useAuth()
  const workspace = me?.workspaces.find((w) => w.id === workspaceId)
  const data = useAgentActivity(() => api.workspaceActivity(workspaceId), workspaceId)
  const running = data?.items.filter((i) => i.state === 'running') ?? []
  const past = data?.items.filter((i) => i.state !== 'running') ?? []
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, padding: '28px 40px 60px', maxWidth: 960 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0, display: 'flex', gap: 10, alignItems: 'center' }}>
          <Bot size={20} aria-hidden /> Mirmera activity
        </h1>
        <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: '6px 0 0', lineHeight: 1.55, maxWidth: 720 }}>
          What Mirmera is doing in {workspace?.name ?? 'this workspace'}: work items it plans from Jira, and charters it
          explores. Everything it makes is a proposal or a draft for a tester to review.
        </p>
        {!data && <p style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>Loading…</p>}
        {data && (
          <>
            <section aria-label="Working now" style={{ marginTop: 22 }}>
              <h2 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 10px' }}>Working now</h2>
              {running.length === 0 && <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>Nothing right now.</p>}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>{running.map((i) => <AgentProgress key={i.id} item={i} />)}</div>
            </section>
            <section aria-label="Recently" style={{ marginTop: 26 }}>
              <h2 style={{ fontSize: 14, fontWeight: 600, margin: '0 0 10px' }}>Recently</h2>
              {past.length === 0 && <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>Nothing yet.</p>}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>{past.map((i) => <AgentProgress key={i.id} item={i} />)}</div>
            </section>
          </>
        )}
      </main>
    </div>
  )
}
