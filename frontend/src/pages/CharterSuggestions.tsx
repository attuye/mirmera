import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Sidebar from '../components/Sidebar'
import { api, Charter, CharterSuggestion, Product } from '../api/client'

const PRIORITY_COLOR: Record<string, string> = { high: 'var(--danger)', medium: 'var(--warning)', low: 'var(--text-tertiary)' }

export default function CharterSuggestions() {
  const [product, setProduct] = useState<Product | null>(null)
  const [diffText, setDiffText] = useState('')
  const [suggestions, setSuggestions] = useState<CharterSuggestion[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [existingCharters, setExistingCharters] = useState<Charter[]>([])
  const navigate = useNavigate()

  useEffect(() => {
    api.listProducts().then((ps) => {
      const p = ps[0] ?? null
      setProduct(p)
      if (p) api.listCharters(p.id).then(setExistingCharters).catch(console.error)
    }).catch(console.error)
  }, [])

  async function generate() {
    if (!product || !diffText.trim()) return
    setBusy(true)
    setError(null)
    try {
      const artifact = await api.createArtifact(product.id, { type: 'diff', label: 'pasted diff', raw_content: diffText })

      const result = await api.suggestCharters(product.id, [artifact.id])
      setSuggestions(result)
    } catch (e) {
      // The backend explains provider problems (missing key, Ollama not running...).
      // A network error means the backend itself isn't reachable.
      const message = e instanceof Error ? e.message : String(e)
      setError(message === 'Failed to fetch'
        ? 'Could not reach the backend. Is it running on http://localhost:8000?'
        : `Could not generate suggestions: ${message}`)
      console.error(e)
    } finally {
      setBusy(false)
    }
  }

  async function accept(s: CharterSuggestion, index: number) {
    if (!product) return
    const charter = await api.saveCharter(product.id, {
      mission_statement: s.mission_statement,
      rationale: s.rationale,
      risk_areas: s.risk_areas,
      priority: s.priority,
      source: 'ai-suggested',
    })
    setExistingCharters((prev) => [...prev, charter])
    setSuggestions((prev) => prev.filter((_, i) => i !== index))
  }

  async function openExisting(c: any) {
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

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <div style={{ flex: 1, padding: '32px 40px', maxWidth: 1100 }}>
        <div style={{ fontSize: 20, fontWeight: 600, marginBottom: 4 }}>Suggest Charters</div>
        {existingCharters.length > 0 && (
          <div style={{ marginBottom: 28 }}>
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 10 }}>
              Existing charters ({existingCharters.length})
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 700 }}>
              {existingCharters.map((c) => (
                <div
                  key={c.id}
                  onClick={() => openExisting(c)}
                  style={{
                    border: '1px solid var(--border)', borderRadius: 9, padding: '10px 14px',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    cursor: c.status === 'active' || c.status === 'proposed' ? 'pointer' : 'default',
                  }}
                >
                  <div style={{ fontSize: 12.5 }}>{c.mission_statement}</div>
                  <span className="mono" style={{ fontSize: 9.5, color: c.status === 'active' ? 'var(--accent)' : 'var(--text-tertiary)', flexShrink: 0, marginLeft: 12 }}>
                    {c.status.toUpperCase()}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div style={{ display: 'flex', gap: 24 }}>
          <div style={{ width: 440 }}>
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 8 }}>
              Paste a diff, spec excerpt, or bug summary
            </div>
            <textarea
              value={diffText}
              onChange={(e) => setDiffText(e.target.value)}
              placeholder="Paste a git diff or spec text here…"
              style={{
                width: '100%', height: 240, background: 'var(--panel-alt)', border: '1px solid var(--border)',
                borderRadius: 9, color: 'var(--text)', padding: '14px 16px', fontFamily: 'var(--font-mono)',
                fontSize: 12, resize: 'vertical',
              }}
            />
            <button onClick={generate} disabled={busy || !diffText.trim()} style={{
              marginTop: 14, width: '100%', background: busy ? 'var(--border)' : 'var(--accent)',
              color: '#0C0D0F', fontSize: 13.5, fontWeight: 600, padding: '12px 0', borderRadius: 8,
            }}>
              {busy ? 'Generating…' : 'Generate Suggestions'}
            </button>
            {error && <div style={{ color: 'var(--danger)', fontSize: 12.5, marginTop: 10 }}>{error}</div>}
          </div>

          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
            {suggestions.length === 0 && !busy && (
              <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>Suggestions will appear here.</div>
            )}
            {suggestions.map((s, i) => (
              <div key={i} style={{ border: '1px solid var(--border)', borderRadius: 11, padding: '17px 20px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
                  <div style={{ fontSize: 14.5, fontWeight: 600 }}>{s.mission_statement}</div>
                  <span className="mono" style={{ fontSize: 10, color: PRIORITY_COLOR[s.priority], flexShrink: 0 }}>
                    {s.priority.toUpperCase()}
                  </span>
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 8, lineHeight: 1.5 }}>{s.rationale}</div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12 }}>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {s.risk_areas.map((r) => (
                      <span key={r} className="mono" style={{ fontSize: 9.5, color: 'var(--danger)', background: 'var(--danger-bg)', padding: '3px 7px', borderRadius: 5 }}>{r}</span>
                    ))}
                  </div>
                  <button onClick={() => accept(s, i)} style={{
                    fontSize: 12, fontWeight: 600, color: '#0C0D0F', background: 'var(--accent)', padding: '5px 14px', borderRadius: 6,
                  }}>
                    Accept
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
