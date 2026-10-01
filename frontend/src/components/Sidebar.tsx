import { Link, useLocation } from 'react-router-dom'

const NAV = [
  { label: 'Dashboard', to: '/dashboard' },
  { label: 'Charters', to: '/charters/suggest' },
  { label: 'Bugs', to: '/bugs' },
  { label: 'Debriefs', to: '/debriefs' },
]

export default function Sidebar() {
  const location = useLocation()
  return (
    <div style={{
      width: 232, flexShrink: 0, background: 'var(--panel-alt)', borderRight: '1px solid var(--border)',
      display: 'flex', flexDirection: 'column', padding: '28px 20px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 40 }}>
        <div style={{
          width: 28, height: 28, borderRadius: 7, background: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, color: '#0C0D0F', fontSize: 15,
        }}>A</div>
        <div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>AddisVerify</div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Explore</div>
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {NAV.map((item) => {
          const active = location.pathname === item.to
          return (
            <Link key={item.to} to={item.to} style={{
              display: 'flex', alignItems: 'center', gap: 12, padding: '9px 12px', borderRadius: 8,
              background: active ? 'var(--accent-bg)' : 'transparent',
              color: active ? 'var(--accent)' : 'var(--text-secondary)',
              fontSize: 13.5, fontWeight: 500,
            }}>
              {item.label}
            </Link>
          )
        })}
      </div>
    </div>
  )
}
