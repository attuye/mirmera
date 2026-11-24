import { useState } from 'react'
import { api, type StoryDetail } from '../api/client'
import { ErrorLine, errorText, input, quietButton } from './ui'

/** Edit a story's title, key, link and description (its acceptance criteria). Mirmera reads the
 *  description fresh each time it suggests risks or explores, so a new rule counts from the next run. */
export default function StoryEditor({ story, trackerName, onSaved, onCancel }: {
  story: StoryDetail; trackerName: string | null; onSaved: (s: StoryDetail) => void; onCancel: () => void
}) {
  const [title, setTitle] = useState(story.title)
  const [key, setKey] = useState(story.key ?? '')
  const [url, setUrl] = useState(story.url ?? '')
  const [description, setDescription] = useState(story.description ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const changed = title !== story.title || key !== (story.key ?? '') || url !== (story.url ?? '') || description !== (story.description ?? '')

  async function save() {
    setSaving(true); setError(null)
    try {
      onSaved(await api.updateStory(story.id, {
        title: title.trim(), description, url, ...(trackerName ? {} : { key }),
      }))
    } catch (e) { setError(errorText(e)) } finally { setSaving(false) }
  }

  const label = { display: 'block', fontSize: 12, color: 'var(--text-secondary)', margin: '12px 0 4px' } as const
  return (
    <form aria-label="Edit story" onSubmit={(e) => { e.preventDefault(); if (changed && title.trim().length >= 3) save() }}
      style={{ marginTop: 14, padding: '14px 16px', border: '1px solid var(--border)', borderRadius: 10, background: 'var(--panel)', maxWidth: 820 }}>
      {trackerName && (
        <p role="note" style={{ margin: '0 0 6px', fontSize: 12.5, color: 'var(--text-secondary)' }}>
          This story comes from {trackerName}. Your edits are kept in Mirmera only, and <b>Refresh from {trackerName}</b> replaces them
          with what {trackerName} says. To keep a change, make it in {trackerName} too.
        </p>
      )}
      <label htmlFor="story-title" style={label}>Title</label>
      <input id="story-title" value={title} onChange={(e) => setTitle(e.target.value)} style={input} />
      <div style={{ display: 'flex', gap: 12 }}>
        {!trackerName && (
          <div style={{ width: 160 }}>
            <label htmlFor="story-key" style={label}>Key</label>
            <input id="story-key" value={key} onChange={(e) => setKey(e.target.value)} placeholder="KYC-12" className="mono" style={input} />
          </div>
        )}
        <div style={{ flex: 1 }}>
          <label htmlFor="story-url" style={label}>Link</label>
          <input id="story-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" style={input} />
        </div>
      </div>
      <label htmlFor="story-description" style={label}>Description and acceptance criteria</label>
      <textarea id="story-description" value={description} onChange={(e) => setDescription(e.target.value)} rows={12}
        style={{ ...input, fontFamily: 'var(--font-ui)', lineHeight: 1.5, resize: 'vertical' }} />
      <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 4 }}>
        One criterion per line, for example "- Signing out removes the customer's session and details." Mirmera reads the rules
        fresh each run, so a new one counts from the next exploration.
      </div>
      <ErrorLine message={error} />
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <button type="submit" disabled={!changed || saving || title.trim().length < 3}
          style={{ background: 'var(--accent)', color: 'white', border: 0, padding: '7px 16px', borderRadius: 7, fontSize: 13 }}>
          {saving ? 'Saving…' : 'Save story'}</button>
        <button type="button" onClick={onCancel} style={quietButton}>Cancel</button>
      </div>
    </form>
  )
}
