// Mirmera action recorder: injected into every page of the recorded browser.
// Turns the tester's clicks, typing, selects, checkboxes, Enter and uploads into
// actions with a Playwright locator, and sends them to Mirmera through the
// window.__mirmeraRecord binding. Only trusted (real user) events are recorded,
// so the app's own programmatic clicks are ignored.
//
// Locator preference, most stable first: test id, label, placeholder,
// role + accessible name, short visible text, then a CSS path.
// Values of sensitive fields (passwords, one-time codes, card data, PINs) are
// never sent: the action is marked masked and the test reads it from an
// environment variable instead.
(() => {
  if (window.__mirmeraRecorderInstalled) return
  window.__mirmeraRecorderInstalled = true

  const send = (action) => {
    try {
      if (typeof window.__mirmeraRecord === 'function') {
        window.__mirmeraRecord({ ...action, url: location.href, in_frame: window !== window.top })
      }
    } catch (_) { /* the recorder must never break the app */ }
  }

  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim()
  const TEXT_MAX = 80
  const SENSITIVE = /pass|pwd|otp|one.?time|token|secret|cvv|cvc|card.?num|cc.?num|pin\b|ssn/i
  const SENSITIVE_AUTOCOMPLETE = /current-password|new-password|one-time-code|cc-number|cc-csc|cc-exp/i
  const lastValue = new WeakMap() // element -> last value recorded, so change + Enter don't double up
  // Enter in a field makes the browser click the form's submit button itself;
  // that click is part of the Enter already recorded, not a second action.
  let lastEnter = { form: null, at: 0 }

  function labelText(el) {
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'))
    const ids = el.getAttribute('aria-labelledby')
    if (ids) {
      const t = ids.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map((n) => n.textContent).join(' ')
      if (clean(t)) return clean(t)
    }
    if (el.labels && el.labels.length) return clean(el.labels[0].textContent)
    return ''
  }

  function roleOf(el) {
    const explicit = el.getAttribute('role')
    if (explicit) return explicit.split(/\s+/)[0]
    const tag = el.tagName.toLowerCase()
    const type = (el.getAttribute('type') || 'text').toLowerCase()
    if (tag === 'button') return 'button'
    if (tag === 'a' && el.hasAttribute('href')) return 'link'
    if (tag === 'select') return 'combobox'
    if (tag === 'textarea') return 'textbox'
    if (/^h[1-6]$/.test(tag)) return 'heading'
    if (tag === 'input') {
      if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button'
      if (type === 'checkbox') return 'checkbox'
      if (type === 'radio') return 'radio'
      if (['text', 'email', 'tel', 'url', 'search', 'number', 'password'].includes(type)) return 'textbox'
    }
    return ''
  }

  function accessibleName(el) {
    const label = labelText(el)
    if (label) return label
    const tag = el.tagName.toLowerCase()
    if (tag === 'input' && ['button', 'submit', 'reset'].includes((el.type || '').toLowerCase())) return clean(el.value)
    if (tag === 'img') return clean(el.getAttribute('alt'))
    const text = clean(el.textContent)
    if (text) return text
    return clean(el.getAttribute('title'))
  }

  function cssPath(el) {
    if (el.id && /^[A-Za-z][\w-]*$/.test(el.id)) return '#' + el.id
    const parts = []
    let node = el
    while (node && node.nodeType === 1 && parts.length < 4) {
      let part = node.tagName.toLowerCase()
      const parent = node.parentElement
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName)
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`
      }
      parts.unshift(part)
      if (node.id && /^[A-Za-z][\w-]*$/.test(node.id)) { parts[0] = '#' + node.id; break }
      node = parent
    }
    return parts.join(' > ')
  }

  function locatorFor(el) {
    const testId = el.getAttribute('data-testid')
    if (testId) return { method: 'testid', value: testId }
    const role = roleOf(el)
    const isField = ['textbox', 'combobox', 'checkbox', 'radio'].includes(role)
    if (isField) {
      const label = labelText(el)
      if (label) return { method: 'label', value: label }
      const ph = clean(el.getAttribute('placeholder'))
      if (ph) return { method: 'placeholder', value: ph }
    }
    const name = accessibleName(el)
    if (role && name && name.length <= TEXT_MAX) return { method: 'role', role, name }
    if (!isField && name && name.length <= TEXT_MAX) return { method: 'text', value: name }
    return { method: 'css', value: cssPath(el) }
  }

  function isSensitive(el) {
    const type = (el.getAttribute('type') || '').toLowerCase()
    if (type === 'password') return true
    if (SENSITIVE_AUTOCOMPLETE.test(el.getAttribute('autocomplete') || '')) return true
    const hints = [el.name, el.id, labelText(el), el.getAttribute('placeholder')].join(' ')
    return SENSITIVE.test(hints)
  }

  function secretName(el) {
    const base = clean(labelText(el) || el.name || el.id || el.getAttribute('placeholder') || 'value')
    return 'MIRMERA_SECRET_' + base.toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 40)
  }

  function recordFill(el) {
    const value = el.value
    if (lastValue.get(el) === value) return
    lastValue.set(el, value)
    if (isSensitive(el)) {
      send({ action: 'fill', locator: locatorFor(el), masked: true, secret: secretName(el) })
    } else {
      send({ action: 'fill', locator: locatorFor(el), value })
    }
  }

  const FIELD = 'input, select, textarea, label, option'
  const CLICKABLE = 'button, a[href], [role], input[type=button], input[type=submit], input[type=reset], input[type=image], summary, [onclick], [tabindex]'

  document.addEventListener('click', (e) => {
    if (!e.isTrusted || !(e.target instanceof Element)) return
    const target = e.target.closest(CLICKABLE) || e.target
    const tag = target.tagName.toLowerCase()
    const type = (target.getAttribute('type') || '').toLowerCase()
    // Fields are recorded by their change (fill, check, select), not by the click into them.
    if (target.matches(FIELD) && !(tag === 'input' && ['button', 'submit', 'reset', 'image'].includes(type))) return
    if (target.closest('label')) return
    if (e.detail === 0 && target.form && target.form === lastEnter.form && performance.now() - lastEnter.at < 200) return
    send({ action: 'click', locator: locatorFor(target) })
  }, true)

  document.addEventListener('change', (e) => {
    if (!e.isTrusted || !(e.target instanceof Element)) return
    const el = e.target
    const tag = el.tagName.toLowerCase()
    const type = (el.getAttribute('type') || '').toLowerCase()
    if (tag === 'select') {
      const option = el.selectedOptions && el.selectedOptions[0]
      send({ action: 'select', locator: locatorFor(el), value: option ? clean(option.textContent) || option.value : el.value })
    } else if (type === 'checkbox' || type === 'radio') {
      send({ action: el.checked ? 'check' : 'uncheck', locator: locatorFor(el) })
    } else if (type === 'file') {
      send({ action: 'upload', locator: locatorFor(el), files: Array.from(el.files || []).map((f) => f.name) })
    } else if (tag === 'input' || tag === 'textarea') {
      recordFill(el)
    }
  }, true)

  document.addEventListener('keydown', (e) => {
    if (!e.isTrusted || e.key !== 'Enter' || !(e.target instanceof Element)) return
    const el = e.target
    if (el.tagName.toLowerCase() !== 'input') return
    recordFill(el) // the value typed so far, before Enter submits the form
    lastEnter = { form: el.form, at: performance.now() }
    send({ action: 'press', locator: locatorFor(el), key: 'Enter' })
  }, true)
})()
