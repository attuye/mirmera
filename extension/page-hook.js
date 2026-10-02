// Mirmera: runs in the page's own JavaScript world (MAIN), only while a tab is
// being recorded. Reports console errors and uncaught errors to content.js
// with window.postMessage; content.js decides whether to forward them.
// Nothing here changes how the page behaves: console.error still logs.
(() => {
  if (window.__mirmeraHook) return
  window.__mirmeraHook = true

  const post = (kind, message) => {
    try { window.postMessage({ __mirmera: 1, kind, message: String(message).slice(0, 4000) }, '*') } catch (_) { /* never break the page */ }
  }
  const text = (a) => {
    if (a instanceof Error) return a.message
    if (typeof a === 'string') return a
    try { return JSON.stringify(a) } catch (_) { return String(a) }
  }

  const original = console.error
  console.error = function (...args) {
    try { post('console_error', args.map(text).join(' ')) } catch (_) { /* ignore */ }
    return original.apply(this, args)
  }
  // Script errors reach window; failed images and scripts don't (they are recorded as failed requests).
  window.addEventListener('error', (e) => { if (e.error || e.message) post('page_error', e.error?.message ?? e.message) })
  window.addEventListener('unhandledrejection', (e) => post('page_error', 'Unhandled rejection: ' + (e.reason?.message ?? String(e.reason))))
})()
