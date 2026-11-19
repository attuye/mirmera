/** While the AI works on suggestions: grey cards in the shape of the result, and a line saying
 *  what is happening, how long it has taken, and that the page can be left (the result is kept). */
export default function SuggestionSkeleton({ label, count = 3 }: { label: string; count?: number }) {
  return (
    <div role="status" aria-label="Working on suggestions" aria-busy="true" style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 820 }}>
      <div style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>{label}</div>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} aria-hidden style={{ border: '1px solid var(--border)', borderRadius: 11, padding: '16px 18px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
            <div className="mm-skeleton" style={{ height: 14, width: `${72 - i * 9}%` }} />
            <div className="mm-skeleton" style={{ height: 10, width: 44 }} />
          </div>
          <div className="mm-skeleton" style={{ height: 10, width: '94%', marginTop: 14 }} />
          <div className="mm-skeleton" style={{ height: 10, width: '58%', marginTop: 7 }} />
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16 }}>
            <div style={{ display: 'flex', gap: 6 }}>
              <div className="mm-skeleton" style={{ height: 16, width: 86 }} />
              <div className="mm-skeleton" style={{ height: 16, width: 64 }} />
            </div>
            <div className="mm-skeleton" style={{ height: 24, width: 66 }} />
          </div>
        </div>
      ))}
    </div>
  )
}
