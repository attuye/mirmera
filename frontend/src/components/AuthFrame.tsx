/** The centred card used by the pages people see before signing in. */
export default function AuthFrame({ title, label, children }: { title: string; label?: string; children: React.ReactNode }) {
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <main style={{ width: '100%', maxWidth: 420 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 24 }}>
          <img src="/mirmera.svg" width={36} height={36} alt="" />
          <div>
            <div style={{ fontWeight: 600, fontSize: 17 }}>Mirmera</div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Context-driven exploratory testing</div>
          </div>
        </div>
        <section aria-label={label ?? title} style={{
          background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 12, padding: '22px 22px 18px',
          display: 'flex', flexDirection: 'column', gap: 12,
        }}>
          <h1 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>{title}</h1>
          {children}
        </section>
      </main>
    </div>
  )
}
