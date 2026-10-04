/** A project's notifications: Telegram chat and/or Slack, which events, a test
 *  message, and what was sent. The bot token and webhook stay in backend/.env. */
import { useEffect, useState } from 'react'
import { api, NotificationSettings as Prefs, NotificationsOverview } from '../api/client'
import { utcDate } from './Evidence'
import { ErrorLine, errorText, fieldLabel, input, primaryButton, quietButton } from './ui'

const STATUS: Record<string, { label: string; color: string }> = {
  sent: { label: 'sent', color: 'var(--accent)' }, mock: { label: 'kept (mock)', color: 'var(--text-secondary)' }, failed: { label: 'failed', color: 'var(--danger)' },
}

export default function NotificationSettings({ projectId }: { projectId: string }) {
  const [data, setData] = useState<NotificationsOverview | null>(null)
  const [form, setForm] = useState<Prefs | null>(null)
  const [busy, setBusy] = useState<'save' | 'test' | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getNotifications(projectId).then((d) => { setData(d); setForm(d.settings) }).catch((e) => setError(errorText(e)))
  }, [projectId])
  if (!data || !form) return <ErrorLine message={error} />

  const dirty = JSON.stringify(form) !== JSON.stringify(data.settings)
  async function run(what: 'save' | 'test') {
    setBusy(what); setError(null); setStatus(null)
    try {
      const d = what === 'save' ? await api.saveNotifications(projectId, form!) : await api.testNotification(projectId)
      setData(d); setForm(d.settings)
      setStatus(what === 'save' ? 'Saved.' : 'Test message sent: see below whether it arrived.')
    } catch (e) { setError(errorText(e)) } finally { setBusy(null) }
  }
  const toggle = (key: string) => setForm({ ...form, events: form.events.includes(key) ? form.events.filter((k) => k !== key) : [...form.events, key] })
  const { telegram, slack, mock } = data.channels
  const none = !data.settings.telegram_chat_id.trim() && !data.settings.slack  // nothing saved to send to

  return (
    <section aria-label="Notifications" style={{ borderTop: '1px solid var(--border-soft)', padding: '22px 0', display: 'grid', gridTemplateColumns: '220px 1fr', gap: 24 }}>
      <div>
        <h2 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Notifications</h2>
        <p style={{ fontSize: 12.5, color: 'var(--text-tertiary)', margin: '4px 0 0', lineHeight: 1.5 }}>
          A message to Telegram or Slack when something needs you. The bot token and the webhook stay in backend/.env.
          {mock && ' Mock mode: messages are kept here, not sent.'}
        </p>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 560 }}>
        <div>
          <label htmlFor="n-telegram" style={fieldLabel}>Telegram chat id</label>
          <input id="n-telegram" value={form.telegram_chat_id} onChange={(e) => setForm({ ...form, telegram_chat_id: e.target.value })}
            style={input} placeholder="e.g. 123456789, or -100… for a group" disabled={!telegram.configured} />
          <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 4 }}>
            {telegram.problem ?? 'Send your bot a message first, then use the chat id (for example from @userinfobot, or the bot’s getUpdates).'}
          </div>
        </div>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
          <input type="checkbox" checked={form.slack} onChange={(e) => setForm({ ...form, slack: e.target.checked })} disabled={!slack.configured} />
          Send to Slack
          <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{slack.problem ?? '(the channel of the webhook in backend/.env)'}</span>
        </label>
        <fieldset style={{ border: 'none', padding: 0, margin: 0 }}>
          <legend style={fieldLabel}>Send a message when</legend>
          {data.events.map((e) => (
            <label key={e.key} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, padding: '3px 0' }}>
              <input type="checkbox" checked={form.events.includes(e.key)} onChange={() => toggle(e.key)} style={{ marginTop: 3 }} />{e.label}
            </label>
          ))}
        </fieldset>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button onClick={() => run('save')} disabled={!dirty || !!busy} style={primaryButton(!dirty || !!busy)}>{busy === 'save' ? 'Saving…' : 'Save notifications'}</button>
          <button onClick={() => run('test')} disabled={dirty || none || !!busy} title={dirty ? 'Save first' : none ? 'Add a Telegram chat id or Slack first' : undefined} style={quietButton}>
            {busy === 'test' ? 'Sending…' : 'Send a test message'}
          </button>
          {status && <span role="status" style={{ fontSize: 12.5, color: 'var(--accent)' }}>{status}</span>}
        </div>
        <ErrorLine message={error} />
        {data.recent.length > 0 && (
          <div>
            <div style={fieldLabel}>Recent</div>
            <ul aria-label="Recent notifications" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {data.recent.map((r, i) => (
                <li key={i} style={{ fontSize: 12, padding: '6px 0', borderTop: '1px solid var(--border-soft)' }}>
                  <span className="mono" style={{ fontSize: 11, color: 'var(--text-quiet)' }}>{utcDate(r.created_at).toLocaleString()}</span>{' '}
                  <span style={{ color: STATUS[r.status].color, fontWeight: 600 }}>{r.channel} {STATUS[r.status].label}</span>{' '}
                  <span style={{ color: 'var(--text-secondary)' }}>{r.text.split('\n')[0].replace(/^Mirmera · [^:]+: /, '')}</span>
                  {r.error && <div style={{ color: 'var(--danger)', fontSize: 11.5 }}>{r.error}</div>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  )
}
