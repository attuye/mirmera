import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, Debrief } from '../api/client'
import Sidebar from '../components/Sidebar'

export default function DebriefPage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const [debrief, setDebrief] = useState<Debrief | null>(null)
  const [duration, setDuration] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [saved, setSaved] = useState(false)


  useEffect(() => {
    if (!sessionId) return
    api.getDebrief(sessionId).then((d) => {
      setDebrief(d)
      setText(d.final_text ?? d.coverage_summary ?? '')
    }).catch(console.error)
    api.getSession(sessionId).then((s) => {
      if (s.ended_at) {
        const start = new Date(s.started_at.endsWith('Z') ? s.started_at : s.started_at + 'Z').getTime()
        const end = new Date(s.ended_at.endsWith('Z') ? s.ended_at : s.ended_at + 'Z').getTime()
        const totalSec = Math.floor((end - start) / 1000)
        const hh = String(Math.floor(totalSec / 3600)).padStart(2, '0')
        const mm = String(Math.floor((totalSec % 3600) / 60)).padStart(2, '0')
        const ss = String(totalSec % 60).padStart(2, '0')
        setDuration(`${hh}:${mm}:${ss}`)
      }
    }).catch(console.error)
  }, [sessionId])

  const navigate = useNavigate()

  async function save() {
    if (!sessionId) return
    await api.updateDebrief(sessionId, text)
    setSaved(true)
    setTimeout(() => navigate('/dashboard'), 800)
  }

  if (!debrief) {
    return (
      <div style={{ display: 'flex', minHeight: '100vh' }}>
        <Sidebar />
        <div style={{ padding: 40, color: 'var(--text-secondary)' }}>Loading debrief…</div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <div style={{ padding: '32px 40px', maxWidth: 800 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <div style={{ fontSize: 20, fontWeight: 600 }}>Session Debrief</div>
          {debrief.ai_draft && (
            <span className="mono" style={{ fontSize: 11, color: 'var(--accent)', background: 'var(--accent-bg)', padding: '4px 10px', borderRadius: 7 }}>
              AI draft — review before saving
            </span>

          )}
          {duration && (
            <div className="mono" style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginBottom: 16 }}>
              Session duration: {duration}
            </div>
          )}
        </div>

        <div className="mono" style={{ fontSize: 10.5, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 8 }}>
          Coverage summary
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          style={{
            width: '100%', minHeight: 220, background: 'var(--panel)', border: '1px solid var(--border)',
            borderRadius: 10, padding: '16px 18px', color: 'var(--text)', fontSize: 13, lineHeight: 1.7,
            resize: 'vertical', fontFamily: 'var(--font-ui)',
          }}
        />

        {debrief.open_questions?.length > 0 && (
          <>
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--text-tertiary)', textTransform: 'uppercase', margin: '18px 0 8px' }}>
              Open questions
            </div>
            <div style={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 10, padding: '13px 16px' }}>
              {debrief.open_questions.map((q, i) => (
                <div key={i} style={{ fontSize: 12.5, color: 'var(--text)', marginBottom: 4 }}>• {q}</div>
              ))}
            </div>
          </>
        )}

        <button onClick={save} style={{
          marginTop: 20, background: 'var(--accent)', color: '#0C0D0F', fontSize: 13, fontWeight: 600,
          padding: '11px 22px', borderRadius: 8,
        }}>
          {saved ? 'Saved ✓' : 'Save Debrief'}
        </button>
      </div>
    </div>
  )
}
