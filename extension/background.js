// Mirmera recorder: background service worker.
//
// Records ONE tab into ONE running Mirmera session:
//   - actions (recorder.js) and console/page errors (page-hook.js) via content.js;
//   - failed requests (4xx/5xx or no response) via chrome.webRequest;
//   - pages visited via chrome.webNavigation;
//   - screenshots via chrome.tabs.captureVisibleTab, notes and bugs from the popup.
// Events are sent to the backend in batches every second. The backend
// sanitizes them again and removes query-string values from URLs.

const DEFAULTS = { apiUrl: 'http://localhost:8000', appUrl: 'http://localhost:5173' }
const FLUSH_MS = 1000
const SCRIPTS = [
  { id: 'mirmera-page-hook', js: ['page-hook.js'], matches: ['<all_urls>'], runAt: 'document_start', world: 'MAIN', allFrames: false, persistAcrossSessions: false },
  { id: 'mirmera-recorder', js: ['content.js', 'recorder.js'], matches: ['<all_urls>'], runAt: 'document_start', allFrames: true, persistAcrossSessions: false },
]

// ---------- state (survives the service worker being stopped) ----------

const getRecording = async () => (await chrome.storage.session.get('recording')).recording ?? null
const setRecording = (recording) => chrome.storage.session.set({ recording })
const settings = async () => ({ ...DEFAULTS, ...(await chrome.storage.local.get(['apiUrl', 'appUrl', 'token'])) })
// What the popup may see: the token itself never leaves the service worker.
const publicSettings = async () => { const { token, ...rest } = await settings(); return { ...rest, hasToken: !!token } }
const setLastError = (lastError) => chrome.storage.session.set({ lastError })

async function api(path, options = {}) {
  const { apiUrl, token } = await settings()
  const res = await fetch(apiUrl.replace(/\/+$/, '') + path, {
    ...options,
    // An API token from Mirmera's Account page: the extension signs in with it.
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options.headers ?? {}) },
  })
  if (res.status === 401) throw new Error('Mirmera needs a token: create one under Account in Mirmera, then paste it in Settings below.')
  if (!res.ok) {
    let detail = ''
    try { detail = (await res.json()).detail } catch (_) { /* not JSON */ }
    throw new Error(typeof detail === 'string' && detail ? detail : `Mirmera answered ${res.status}`)
  }
  return res.json()
}

// ---------- sending events ----------

let queue = []
let timer = null
let lastNavigation = null

function push(event) {
  queue.push(event)
  if (!timer) timer = setTimeout(flush, FLUSH_MS)
}

async function flush() {
  timer = null
  const rec = await getRecording()
  if (!rec) { queue = []; return }
  const batch = queue.splice(0, 200)
  if (!batch.length) return
  try {
    await api(`/sessions/${rec.sessionId}/extension/events`, { method: 'POST', body: JSON.stringify({ events: batch }) })
    await setLastError(null)
  } catch (err) {
    await setLastError(`Not sent: ${err.message}`)
    if (/has ended/.test(err.message)) await stop()  // the session was ended in Mirmera
  }
  if (queue.length && !timer) timer = setTimeout(flush, FLUSH_MS)
}

async function forRecordedTab(tabId, fn) {
  const rec = await getRecording()
  if (rec && tabId === rec.tabId) fn(rec)
}

// ---------- start / stop ----------

async function start(tabId, sessionId) {
  const tab = await chrome.tabs.get(tabId)
  if (!/^https?:/.test(tab.url ?? '')) throw new Error('Open the app you want to test in this tab first (an http or https page).')
  const previous = await getRecording()
  if (previous) await stop()

  await setRecording({ tabId, sessionId, windowId: tab.windowId, startedAt: Date.now() })
  await setLastError(null)
  try { await chrome.scripting.unregisterContentScripts({ ids: SCRIPTS.map((s) => s.id) }) } catch (_) { /* none yet */ }
  await chrome.scripting.registerContentScripts(SCRIPTS)
  // The page is already loaded: inject into it now too (the scripts guard against running twice).
  await chrome.scripting.executeScript({ target: { tabId }, files: ['page-hook.js'], world: 'MAIN' })
  await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ['content.js', 'recorder.js'] })

  lastNavigation = tab.url
  push({ kind: 'navigation', url: tab.url })
  await chrome.action.setBadgeText({ text: 'REC', tabId })
  await chrome.action.setBadgeBackgroundColor({ color: '#d93025', tabId })
}

async function stop() {
  const rec = await getRecording()
  await flush()
  await setRecording(null)
  lastNavigation = null
  try { await chrome.scripting.unregisterContentScripts({ ids: SCRIPTS.map((s) => s.id) }) } catch (_) { /* already gone */ }
  if (rec) {
    try { await chrome.action.setBadgeText({ text: '', tabId: rec.tabId }) } catch (_) { /* tab closed */ }
    try { await api(`/sessions/${rec.sessionId}/extension/stop`, { method: 'POST' }) } catch (_) { /* backend down: it times out */ }
  }
}

// ---------- what Chrome tells us about the recorded tab ----------
// Listeners are registered at the top level so Chrome can wake the worker for them.

chrome.webRequest.onCompleted.addListener((d) => {
  if (d.statusCode < 400 || d.url.endsWith('/favicon.ico')) return
  forRecordedTab(d.tabId, () => push({ kind: 'failed_request', url: d.url, method: d.method, status: d.statusCode }))
}, { urls: ['<all_urls>'] })

chrome.webRequest.onErrorOccurred.addListener((d) => {
  if (d.error.includes('ERR_ABORTED') || d.url.endsWith('/favicon.ico')) return
  forRecordedTab(d.tabId, () => push({ kind: 'failed_request', url: d.url, method: d.method, error: d.error }))
}, { urls: ['<all_urls>'] })

const onNavigation = (d) => {
  if (d.frameId !== 0 || d.url === lastNavigation || !/^https?:/.test(d.url)) return
  forRecordedTab(d.tabId, () => { lastNavigation = d.url; push({ kind: 'navigation', url: d.url }) })
}
chrome.webNavigation.onCommitted.addListener(onNavigation)
chrome.webNavigation.onHistoryStateUpdated.addListener(onNavigation)  // single-page apps

chrome.tabs.onRemoved.addListener((tabId) => forRecordedTab(tabId, () => stop()))

// ---------- popup commands ----------

async function screenshot(rec, caption, bugId) {
  const tab = await chrome.tabs.get(rec.tabId)
  if (!tab.active) throw new Error('Switch to the recorded tab to take a screenshot.')
  const image = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' })
  return api(`/sessions/${rec.sessionId}/extension/screenshot`, {
    method: 'POST', body: JSON.stringify({ image, caption, bug_id: bugId ?? null, url: tab.url }),
  })
}

async function popup(msg) {
  const rec = await getRecording()
  switch (msg.cmd) {
    case 'status': {
      const { lastError } = await chrome.storage.session.get('lastError')
      let tab = null
      if (rec) { try { tab = await chrome.tabs.get(rec.tabId) } catch (_) { /* closed */ } }
      return { recording: rec, tab: tab && { title: tab.title, url: tab.url }, lastError: lastError ?? null, settings: await publicSettings() }
    }
    case 'sessions': return api('/sessions/active')
    case 'start': await start(msg.tabId, msg.sessionId); return { ok: true }
    case 'stop': await stop(); return { ok: true }
    case 'settings':
      await chrome.storage.local.set({ apiUrl: msg.apiUrl, appUrl: msg.appUrl, ...(msg.token ? { token: msg.token } : {}) })
      return { ok: true }
    case 'note': {
      if (!rec) throw new Error('Not recording.')
      return api(`/sessions/${rec.sessionId}/notes`, { method: 'POST', body: JSON.stringify({ tag: msg.tag, content: msg.content }) })
    }
    case 'bug': {
      if (!rec) throw new Error('Not recording.')
      await flush()  // so the bug's evidence includes what just happened
      const bug = await api(`/sessions/${rec.sessionId}/bugs`, { method: 'POST', body: JSON.stringify({ title: msg.title, severity: 'medium' }) })
      await api(`/sessions/${rec.sessionId}/notes`, { method: 'POST', body: JSON.stringify({ tag: 'bug', content: msg.title }) })
      let warning = null
      try { await screenshot(rec, `When logged: ${msg.title}`, bug.id) } catch (err) { warning = `Bug saved without a screenshot: ${err.message}` }
      return { ok: true, warning }
    }
    case 'screenshot': {
      if (!rec) throw new Error('Not recording.')
      return screenshot(rec, msg.caption)
    }
    default: throw new Error(`Unknown command ${msg.cmd}`)
  }
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg?.type === 'am-i-recorded') {
    getRecording().then((rec) => reply({ recording: !!rec && sender.tab?.id === rec.tabId }))
    return true
  }
  if (msg?.type === 'event') {
    forRecordedTab(sender.tab?.id, () => {
      const e = msg.event
      if (e?.kind === 'action' || e?.kind === 'console_error' || e?.kind === 'page_error') push(e)
    })
    return false
  }
  if (msg?.type === 'popup' && !sender.tab?.url?.startsWith('http')) {  // only the extension's own pages
    popup(msg).then(reply, (err) => reply({ error: err.message ?? String(err) }))
    return true
  }
  return false
})
