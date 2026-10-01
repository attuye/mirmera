import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import { api, Charter, Product } from '../api/client'

const PRIORITY_COLOR: Record<string, string> = { high: 'var(--danger)', medium: 'var(--warning)', low: 'var(--text-tertiary)' }
const STATUS_STYLE: Record<string, { bg: string; fg: string }> = {
  active: { bg: 'var(--accent-bg)', fg: 'var(--accent)' },
  proposed: { bg: 'var(--warning-bg)', fg: 'var(--warning)' },
  done: { bg: '#1A1D22', fg: 'var(--text-secondary)' },
}

export default function Dashboard() {
  const [product, setProduct] = useState<Product | null>(null)
  const [charters, setCharters] = useState<Charter[]>([])
  const [loading, setLoading] = useState(true)

  const navigate = useNavigate()

  async function openCharter(c: Charter) {
    if (c.status === 'active') {
      try {
        const session = await api.getActiveSession(c.id)
        navigate(`/sessions/${session.id}`)
      } catch {
        console.warn('Charter marked active but no open session found')
      }
      return
    }
    if (c.status === 'proposed') {
      const session = await api.startSession(c.id, 'attu')
      navigate(`/sessions/${session.id}`)
    }
  }

  useEffect(() => {
    async function load() {
      // Scaffold convenience: use the first product, or create one on first run.
      let products = await api.listProducts()
      if (products.length === 0) {
        await api.createProduct('AddisVerify KYC Platform', 'org_default')
        products = await api.listProducts()
      }
      const p = products[0]
      setProduct(p)
      setCharters(await api.listCharters(p.id))
      setLoading(false)
    }
    load().catch(console.error)
  }, [])

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <div style={{ flex: 1, padding: '32px 40px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 28 }}>
          <div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)', textTransform: 'uppercase', letterSpacing: 0.6, marginBottom: 6 }}>Project</div>
            <div style={{ fontSize: 24, fontWeight: 600 }}>{product?.name ?? '—'}</div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>Context-driven exploratory testing workspace</div>
          </div>
          <Link to="/charters/suggest" style={{
            background: 'var(--accent)', color: '#0C0D0F', fontSize: 13.5, fontWeight: 600,
            padding: '11px 18px', borderRadius: 8,
          }}>+ New Session</Link>
        </div>

        {loading ? (
          <div style={{ color: 'var(--text-secondary)' }}>Loading…</div>
        ) : (
          <div style={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, padding: '20px 22px', maxWidth: 700 }}>
            <div style={{ fontSize: 14.5, fontWeight: 600, marginBottom: 14 }}>Charters</div>
            {charters.length === 0 && (
              <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                No charters yet. <Link to="/charters/suggest">Generate some from an artifact →</Link>
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {charters.map((c) => {
                const status = STATUS_STYLE[c.status] ?? STATUS_STYLE.proposed
                return (
                  // <div key={c.id} style={{ border: '1px solid var(--border)', borderRadius: 9, padding: '13px 14px' }}>
                  <div
                    key={c.id}
                    onClick={() => openCharter(c)}
                    style={{
                      border: '1px solid var(--border)', borderRadius: 9, padding: '13px 14px',
                      cursor: c.status === 'active' || c.status === 'proposed' ? 'pointer' : 'default',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                      <div style={{ fontSize: 13, fontWeight: 500 }}>{c.mission_statement}</div>
                      <div className="mono" style={{ fontSize: 9.5, background: status.bg, color: status.fg, padding: '3px 7px', borderRadius: 5, whiteSpace: 'nowrap' }}>
                        {c.status.toUpperCase()}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 9, alignItems: 'center' }}>
                      <span className="mono" style={{ fontSize: 9.5, color: 'var(--question)', background: '#221C2B', padding: '3px 7px', borderRadius: 5 }}>
                        {c.source === 'ai-suggested' ? 'AI-suggested' : 'Manual'}
                      </span>
                      <span className="mono" style={{ fontSize: 9.5, color: PRIORITY_COLOR[c.priority] }}>{c.priority} priority</span>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
