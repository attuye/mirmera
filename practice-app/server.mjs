// AddisVerify KYC demo: a small app with planted bugs, for practising with
// Mirmera. No dependencies: run `node practice-app/server.mjs` and open
// http://localhost:4000. Data lives in memory and resets on restart.
//
// Don't read the code too closely before testing: the bugs are listed in
// practice-app/ANSWERS.md for afterwards.
import http from 'node:http'

const PORT = Number(process.env.PORT ?? 4000)
const verified = [] // { name, docType, number, expiry }

const page = (title, body) => `<!doctype html>
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
<body><header><strong>AddisVerify</strong><a href="/">Home</a><a href="/verify">Verify identity</a><a href="/documents">My documents</a></header>
<main>${body}</main></body></html>`

const HOME = page('Home', `
  <h1>Verify your identity</h1>
  <p>Open an account in minutes. We accept a National ID, a Passport or a Driver's licence.</p>
  <p><a href="/verify">Start verification →</a></p>`)

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
    })
  </script>`)

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
  if (req.method === 'GET' && url.pathname === '/') return res.writeHead(200, { 'Content-Type': 'text/html' }).end(HOME)
  if (req.method === 'GET' && url.pathname === '/verify') return res.writeHead(200, { 'Content-Type': 'text/html' }).end(VERIFY)
  if (req.method === 'GET' && url.pathname === '/documents') return res.writeHead(200, { 'Content-Type': 'text/html' }).end(DOCUMENTS)
  if (req.method === 'POST' && url.pathname === '/api/verify') {
    const body = await readBody(req)
    if (body.docType === 'Passport') return json(res, 500, { error: 'mrz_parser_unavailable' })
    verified.push(body)
    return json(res, 200, { ok: true })
  }
  if (req.method === 'GET' && url.pathname === '/api/documents') {
    return json(res, 200, { total: verified.length, docs: verified.map(({ docType, number }) => ({ docType, number })) })
  }
  res.writeHead(404, { 'Content-Type': 'text/plain' }).end('Not found')
}).listen(PORT, () => console.log(`AddisVerify KYC demo on http://localhost:${PORT}`))
