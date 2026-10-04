// Mirmera recorder popup. Everything goes through the background worker.
const $ = (id) => document.getElementById(id)
const send = (cmd, extra = {}) => new Promise((resolve, reject) => {
  chrome.runtime.sendMessage({ type: 'popup', cmd, ...extra }, (reply) => {
    if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message))
    if (reply?.error) return reject(new Error(reply.error))
    resolve(reply)
  })
})
const say = (text, isError = false) => { $('message').textContent = text ?? ''; $('message').className = isError ? 'error' : '' }

// The tab to record: the one this popup was opened over. (popup.html?tabId=N picks another, for tests.)
async function targetTab() {
  const fromUrl = Number(new URLSearchParams(location.search).get('tabId'))
  if (fromUrl) return fromUrl
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true })
  return tab?.id
}

let sessions = []

async function render() {
  const status = await send('status')
  $('api').value = status.settings.apiUrl
  $('app').value = status.settings.appUrl
  $('token').placeholder = status.settings.hasToken ? 'Saved (paste a new one to replace it)' : 'mm_…'
  const rec = status.recording
  $('state').textContent = rec ? 'REC' : 'Not recording'
  $('state').className = rec ? 'pill rec' : 'pill'
  $('idle').hidden = !!rec
  $('recording').hidden = !rec
  if (status.lastError) say(status.lastError, true)

  if (rec) {
    if (!sessions.length) { try { sessions = await send('sessions') } catch (_) { /* shown below */ } }
    const s = sessions.find((x) => x.id === rec.sessionId)
    $('mission').textContent = s ? `${s.project_name} · ${s.charter_mission}` : 'Recording'
    $('tab').textContent = status.tab ? `Tab: ${status.tab.title || status.tab.url}` : 'The recorded tab was closed.'
    const base = status.settings.appUrl.replace(/\/+$/, '')
    $('open').href = s ? `${base}/projects/${s.project_id}/sessions/${rec.sessionId}` : `${base}/sessions/${rec.sessionId}`
    return
  }
  try {
    sessions = await send('sessions')
  } catch (err) {
    say(`Can't reach Mirmera at ${status.settings.apiUrl}: ${err.message}. Is the backend running? (Settings below)`, true)
    sessions = []
  }
  $('session').innerHTML = ''
  for (const s of sessions) {
    const o = document.createElement('option')
    o.value = s.id
    o.textContent = `${s.paused ? '(paused) ' : ''}${s.project_name} · ${s.charter_mission}`
    $('session').append(o)
  }
  $('no-sessions').hidden = sessions.length > 0
  $('start').disabled = sessions.length === 0
}

async function run(fn) {
  say('')
  try { await fn() } catch (err) { say(err.message, true) }
  await render().catch((err) => say(err.message, true))
}

$('start').onclick = () => run(async () => { await send('start', { tabId: await targetTab(), sessionId: $('session').value }); say('Recording this tab.') })
$('stop').onclick = () => run(async () => { await send('stop'); say('Stopped. The session keeps everything recorded.') })
$('shot').onclick = () => run(async () => { await send('screenshot', { caption: '' }); say('Screenshot saved.') })
$('log').onclick = () => run(async () => {
  const content = $('note').value.trim()
  if (!content) throw new Error('Write something first.')
  const tag = document.querySelector('input[name=tag]:checked').value
  if (tag === 'bug') {
    const r = await send('bug', { title: content })
    say(r.warning ?? 'Bug logged, with a screenshot.')
  } else {
    await send('note', { tag, content })
    say('Note logged.')
  }
  $('note').value = ''
})
document.querySelectorAll('input[name=tag]').forEach((r) => r.addEventListener('change', () => {
  $('log').textContent = document.querySelector('input[name=tag]:checked').value === 'bug' ? 'Log bug' : 'Log note'
}))
$('note').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) $('log').click() })
$('save').onclick = () => run(async () => {
  await send('settings', {
    apiUrl: $('api').value.trim() || 'http://localhost:8000', appUrl: $('app').value.trim() || 'http://localhost:5173',
    token: $('token').value.trim() || undefined,
  })
  $('token').value = ''
  sessions = []
  say('Settings saved.')
})

render().catch((err) => say(err.message, true))
