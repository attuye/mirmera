// AddisVerify KYC demo: a small app with planted bugs, for practising with
// Mirmera. No dependencies: run `node practice-app/server.mjs` and open
// http://localhost:4000. Data lives in memory and resets on restart.
//
// Don't read the code too closely before testing: the bugs are listed in
// practice-app/ANSWERS.md for afterwards.
//
// PLANTED=classic runs it with the first four bugs only, as the agent evals do (so their
// scores stay comparable over time). By default it also has bugs 5 to 9: what it keeps in
// the browser, its result page and its data.
import http from 'node:http'

const PORT = Number(process.env.PORT ?? 4000)
const CLASSIC = process.env.PLANTED === 'classic'
const today = () => new Date().toISOString().slice(0, 10)
const verified = CLASSIC ? [] : [ // { id, name, docType, number, expiry, status }
  // Customers verified before today, as the bank's data has them.
  { id: 1, name: 'Almaz Tesfaye', docType: 'National ID', number: 'ET0012345', expiry: '2029-04-30', status: 'verified' },
  { id: 2, name: 'Dawit Haile', docType: 'Passport', number: 'EP2233445', expiry: '2027-08-15', status: 'verified' },
  { id: 3, name: 'Hanna Girma', docType: "Driver's licence", number: 'DL778899', expiry: '2025-03-01', status: 'verified' },
  { id: 4, name: 'Yonas Bekele', docType: 'National ID', number: 'ET0098765', expiry: null, status: 'verified' },
  { id: 5, name: 'Selam Mekonnen', docType: 'National ID', number: 'ET0055511', expiry: '2030-01-31', status: 'verified' },
  { id: 6, name: 'Kebede Alemu', docType: 'Passport', number: 'EP9988776', expiry: '2028-11-20', status: 'verified' },
]
let nextId = verified.length + 1

// The demo signs you in as Abebe on every page (no password needed), so there is a session to sign out of.
const SESSION_JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhYmViZSIsIm5hbWUiOiJBYmViZSBLZWJlZGUifQ.ZGVtby1zaWduYXR1cmUtbm90LXJlYWw'
const signedIn = CLASSIC ? '' : `<span id="who">Signed in as Abebe</span><a href="/signout">Sign out</a>
  <script>localStorage.setItem('auth', '${SESSION_JWT}'); localStorage.setItem('user', JSON.stringify({ name: 'Abebe Kebede' }))</script>`

const page = (title, body, nav = signedIn) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${title} · AddisVerify</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #f5f6f8; color: #1d2330 }
  header { background: #12324f; color: white; padding: 14px 32px; display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center }
  @media (max-width: 600px) { header { padding: 12px 16px } main { margin: 16px 12px; padding: 20px 16px } input, select { max-width: 100% } }
  header a { color: #cfe3ff; text-decoration: none } header strong { margin-right: auto }
  main { max-width: 560px; margin: 32px auto; background: white; padding: 28px 32px; border-radius: 10px; box-shadow: 0 1px 3px #0002 }
  label { display: block; font-size: 14px; margin: 14px 0 4px }
  input, select { width: 100%; padding: 8px 10px; font-size: 15px; box-sizing: border-box; border: 1px solid #c4cad4; border-radius: 6px }
  input[type=checkbox] { width: auto }
  .check { display: flex; gap: 8px; align-items: center; margin-top: 16px } .check label { margin: 0 }
  button { margin-top: 20px; background: #1565c0; color: white; border: 0; padding: 10px 22px; font-size: 15px; border-radius: 6px; cursor: pointer }
  [role=status] { margin-top: 16px; font-weight: 600 } .ok { color: #1b7a3a } .bad { color: #b3261e }
  li { margin: 6px 0 }
</style></head>
<body><header><strong>AddisVerify</strong><a href="/">Home</a><a href="/verify">Verify identity</a><a href="/documents">My documents</a>${nav}</header>
<main>${body}</main></body></html>`

const HOME = page('Home', `
  <h1>Verify your identity</h1>
  <p>Open an account in minutes. We accept a National ID, a Passport or a Driver's licence.</p>
  <p><a href="/verify">Start verification →</a></p>`)

// Not in classic mode: the form keeps a draft in the browser, and a verified customer sees their result.
const RESULT_SCRIPT = `
  <section id="your" hidden><h2>Your verification</h2><dl id="summary"></dl></section>
  <script>
    const draft = () => localStorage.setItem('kyc_draft', JSON.stringify({ fullName: v('name'), phone: v('phone'),
      documentType: v('doctype'), documentNumber: v('docnum'), expiryDate: v('expiry') }))
    document.getElementById('kyc').addEventListener('input', draft)
    document.getElementById('kyc').addEventListener('change', draft)
    function v(id) { return document.getElementById(id).value.trim() }
    async function showResult(id) {
      if (!id) return
      const d = await fetch('/api/verification/' + id).then((r) => r.json())
      const shown = [
        ['Status', 'Verified'],
        ['Full name', d.fullName],
        ['Document type', d.documentType],
        ['Expiry date', new Date(d.expiryDate).toLocaleDateString('en-GB', { timeZone: 'America/New_York' })],
      ]
      document.getElementById('summary').innerHTML = shown.map(([k, x]) => '<dt>' + k + '</dt><dd>' + x + '</dd>').join('')
      document.getElementById('your').hidden = false
    }
  </script>`

const VERIFY = page('Verify identity', `
  <h1>Verify identity</h1>
  <form id="kyc" novalidate>
    <label for="name">Full name</label><input id="name" autocomplete="name">
    <label for="phone">Phone number</label><input id="phone" placeholder="+251 9xx xxx xxx">
    <label for="password">Create a password</label><input id="password" type="password">
    <label for="doctype">Document type</label>
    <select id="doctype"><option>National ID</option><option>Passport</option><option>Driver's licence</option></select>
    <label for="docnum">Document number</label><input id="docnum">
    <label for="expiry">Expiry date</label><input id="expiry" type="date">
    <div class="check"><input type="checkbox" id="terms"><label for="terms">I accept the terms</label></div>
    <button type="submit">Verify</button>
  </form>
  <p role="status" id="result"></p>
  <script>
    document.getElementById('kyc').addEventListener('submit', async (e) => {
      e.preventDefault()
      const out = document.getElementById('result')
      const v = (id) => document.getElementById(id).value.trim()
      if (!document.getElementById('terms').checked) { out.className = 'bad'; out.textContent = 'Please accept the terms.'; return }
      if (!/^[A-Za-z .'-]+$/.test(v('name'))) { out.className = 'bad'; out.textContent = 'Name must use letters only.'; return }
      if (!v('docnum') || !v('expiry')) { out.className = 'bad'; out.textContent = 'Document number and expiry date are required.'; return }
      out.className = ''; out.textContent = 'Checking…'
      const r = await fetch('/api/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: v('name'), docType: v('doctype'), number: v('docnum'), expiry: v('expiry') }) })
        .catch(() => ({ ok: false }))  // no connection: said like any other failure
      if (r.ok) { out.className = 'ok'; out.textContent = 'Verified. Welcome to AddisVerify!' }
      else { out.className = 'bad'; out.textContent = 'Something went wrong. Please try again later.' }
      if (r.ok && r.json && window.showResult) showResult((await r.json()).id)
    })
  </script>${CLASSIC ? '' : RESULT_SCRIPT}`)

const SIGNED_OUT = page('Signed out', `
  <h1>You're signed out</h1>
  <p>Thanks for using AddisVerify. <a href="/">Sign in again</a></p>
  <script>localStorage.removeItem('auth')</script>`, '<a href="/">Sign in</a>')

const DOCUMENTS = page('My documents', `
  <h1>My documents</h1>
  <p id="count">Loading…</p>
  <ul id="list"></ul>
  <script>
    fetch('/api/documents').then((r) => r.json()).then((data) => {
      document.getElementById('count').textContent = data.total + ' verified document(s)'
      document.getElementById('list').innerHTML = data.items.map((d) => '<li>' + d.docType + ' ' + d.number + '</li>').join('')
    })
  </script>`)

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' }).end(JSON.stringify(body))
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = ''
    req.on('data', (c) => (data += c))
    req.on('end', () => { try { resolve(JSON.parse(data || '{}')) } catch { resolve({}) } })
  })
}

http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`)
  const html = (body) => res.writeHead(200, { 'Content-Type': 'text/html', ...(CLASSIC ? {} : {
    'Set-Cookie': 'sid=demo-session-7f3a9c1e2b; Path=/; SameSite=Lax' }) }).end(body)
  if (!CLASSIC && req.method === 'GET' && url.pathname === '/signout') {
    return res.writeHead(302, { Location: '/signed-out' }).end()  // the session cookie is never cleared
  }
  if (!CLASSIC && req.method === 'GET' && url.pathname === '/signed-out') {
    return res.writeHead(200, { 'Content-Type': 'text/html' }).end(SIGNED_OUT)
  }
  if (!CLASSIC && req.method === 'GET' && url.pathname.startsWith('/api/verification/')) {
    const d = verified.find((x) => String(x.id) === url.pathname.split('/').pop())
    if (!d) return json(res, 404, { error: 'not_found' })
    return json(res, 200, { id: d.id, status: d.status, fullName: d.name, documentType: d.docType,
      documentNumber: d.number, expiryDate: d.expiry })
  }
  if (req.method === 'GET' && url.pathname === '/') return CLASSIC ? res.writeHead(200, { 'Content-Type': 'text/html' }).end(HOME) : html(HOME)
  if (req.method === 'GET' && url.pathname === '/verify') return CLASSIC ? res.writeHead(200, { 'Content-Type': 'text/html' }).end(VERIFY) : html(VERIFY)
  if (req.method === 'GET' && url.pathname === '/documents') return CLASSIC ? res.writeHead(200, { 'Content-Type': 'text/html' }).end(DOCUMENTS) : html(DOCUMENTS)
  if (req.method === 'POST' && url.pathname === '/api/verify') {
    const body = await readBody(req)
    if (body.docType === 'Passport') return json(res, 500, { error: 'mrz_parser_unavailable' })
    if (CLASSIC) {
      verified.push(body)
      return json(res, 200, { ok: true })
    }
    const id = nextId++  // the expiry is never checked here (bug 2); the result's status is
    verified.push({ id, name: body.name, docType: body.docType, number: body.number, expiry: body.expiry,
      status: body.expiry && body.expiry < today() ? 'rejected' : 'verified' })
    return json(res, 200, { ok: true, id })
  }
  if (req.method === 'GET' && url.pathname === '/api/documents') {
    if (CLASSIC) return json(res, 200, { total: verified.length, docs: verified.map(({ docType, number }) => ({ docType, number })) })
    return json(res, 200, { total: verified.length, docs: verified.map(({ id, docType, number, expiry, status }) =>
      ({ id, docType, number, status, expiryDate: expiry })) })
  }
  res.writeHead(404, { 'Content-Type': 'text/plain' }).end('Not found')
}).listen(PORT, () => console.log(`AddisVerify KYC demo on http://localhost:${PORT}`))
