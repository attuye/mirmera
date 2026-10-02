// Mirmera: runs in every frame of a page (isolated world) while recording is
// on, just before recorder.js. Asks the background whether this tab is the
// recorded one, then forwards recorded actions (from recorder.js) and errors
// (from page-hook.js) to it. Other tabs send nothing.
(() => {
  if (window.__mirmeraContent) return
  window.__mirmeraContent = true
  // Until the background answers, we don't know whether this tab is recorded:
  // keep early events (errors logged while the page loads) and decide then.
  let recording = null // null = not known yet
  let early = []

  const send = (event) => {
    try { chrome.runtime.sendMessage({ type: 'event', event }) } catch (_) { recording = false }
  }
  const forward = (event) => {
    if (recording === null) { if (early.length < 200) early.push(event); return }
    if (recording) send(event)
  }

  try {
    chrome.runtime.sendMessage({ type: 'am-i-recorded' }, (reply) => {
      void chrome.runtime.lastError
      recording = !!(reply && reply.recording)
      if (recording) early.forEach(send)
      early = []
    })
  } catch (_) { recording = false /* extension reloaded: stay silent */ }

  // recorder.js reports through this function (the recorded browser uses a Playwright binding of the same name).
  window.__mirmeraRecord = (action) => forward({ kind: 'action', url: action.url, action })

  if (window === window.top) {
    window.addEventListener('message', (e) => {
      const d = e.data
      if (e.source !== window || !d || d.__mirmera !== 1) return
      if (d.kind === 'console_error' || d.kind === 'page_error') forward({ kind: d.kind, message: d.message, url: location.href })
    })
  }
})()
