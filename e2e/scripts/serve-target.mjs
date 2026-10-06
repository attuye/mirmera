// A tiny stand-in for "the app under test" in the browser-runner tests. It
// misbehaves on purpose: console errors on load and an API call that fails.
import http from 'node:http'
import { createSign, generateKeyPairSync } from 'node:crypto'

const port = Number(process.env.E2E_TARGET_PORT ?? 5190)

const home = `<!doctype html><html lang="en"><title>KYC upload</title>
<style>body{font-family:sans-serif;padding:40px;background:#f6f7f9}</style>
<h1>Upload your ID</h1><p>Drop a photo of your national ID here.</p><div id="widget">Loading upload widget…</div>
<form action="/" method="get"><label for="q">Search documents</label> <input id="q" name="q"> <button>Search</button></form>
<p role="status" id="found"></p>
<script>
  const q = new URLSearchParams(location.search).get('q')
  if (q) document.getElementById('found').textContent = 'No documents match "' + q + '"'

  console.error('Upload widget failed to init')
  console.error('Upload widget failed to init')
  fetch('/api/documents')
</script>`

// A sign-up page with accessibility problems on purpose, and a slow API call.
const signup = `<!doctype html><title>Sign up</title>
<body style="background:#fff;font-family:sans-serif;padding:40px"><h1>Create your account</h1>
<input id="phone" placeholder="Phone number"> <button style="width:24px;height:24px"></button>
<p style="color:#c8c8c8">By signing up you accept the terms</p>
<script>fetch('/api/slow')</script></body>`

const openapi = {
  openapi: '3.0.3',
  info: { title: 'Target API', version: '1.0' },
  servers: [{ url: `http://localhost:${port}` }],
  components: { securitySchemes: { bearer: { type: 'http', scheme: 'bearer' } } },
  paths: {
    '/api/profile': { get: { tags: ['Account'], summary: 'Who is signed in', security: [{ bearer: [] }] } },
    '/api/documents': {
      get: { tags: ['Documents'], summary: 'List documents' },
      post: {
        tags: ['Documents'], summary: 'Upload a document',
        requestBody: { content: { 'application/json': { schema: { type: 'object', properties: { type: { type: 'string', enum: ['passport'] }, number: { type: 'string', example: 'EP1234567' } } } } } },
      },
    },
  },
}

// A stand-in for Atlassian, for the Jira app spec: it publishes a signing key (as the
// Forge JWKS) and signs Forge invocation tokens for whichever installation the test names.
// The key pair is made fresh on every start; nothing secret is kept in the repo.
const forge = generateKeyPairSync('rsa', { modulusLength: 2048 })
const jwks = { keys: [{ ...forge.publicKey.export({ format: 'jwk' }), kid: 'e2e', alg: 'RS256', use: 'sig' }] }
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url')
function forgeToken({ installationId, appId }) {
  const now = Math.floor(Date.now() / 1000)
  const body = `${b64({ alg: 'RS256', typ: 'JWT', kid: 'e2e' })}.${b64({
    iss: 'forge/invocation-token', aud: appId, iat: now, nbf: now, exp: now + 300, principal: 'e2e-jira-admin',
    app: { installationId, id: appId, environment: { type: 'DEVELOPMENT', id: 'e2e' } }, context: { cloudId: 'e2e-cloud' },
  })}`
  return `${body}.${createSign('RSA-SHA256').update(body).sign(forge.privateKey).toString('base64url')}`
}

http
  .createServer((req, res) => {
    const path = new URL(req.url, 'http://localhost').pathname
    if (path === '/') {
      res.writeHead(200, { 'Content-Type': 'text/html' }).end(home)
    } else if (path === '/signup') {
      res.writeHead(200, { 'Content-Type': 'text/html' }).end(signup)
    } else if (path === '/api/slow') {
      setTimeout(() => res.writeHead(200, { 'Content-Type': 'application/json' }).end('{}'), 2300)
    } else if (path === '/openapi.json') {  // for API exploration: the target describes itself
      res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(openapi))
    } else if (path === '/api/profile') {  // for API exploration: needs a token
      if (req.headers.authorization === 'Bearer demo-token') {
        res.writeHead(200, { 'Content-Type': 'application/json' }).end('{"name":"Abebe","documents":2}')
      } else {
        res.writeHead(401, { 'Content-Type': 'application/json' }).end('{"error":"sign in first"}')
      }
    } else if (path === '/fake-forge/jwks.json') {
      res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(jwks))
    } else if (path === '/fake-forge/token') {  // ?installation=...&app=...
      const q = new URL(req.url, 'http://localhost').searchParams
      res.writeHead(200, { 'Content-Type': 'text/plain' }).end(forgeToken({ installationId: q.get('installation'), appId: q.get('app') }))
    } else if (path === '/api/documents') {
      res.writeHead(500, { 'Content-Type': 'application/json' }).end('{"error":"storage unavailable"}')
    } else {
      res.writeHead(404, { 'Content-Type': 'text/plain' }).end('not found')
    }
  })
  .listen(port, () => console.log(`Target app on http://localhost:${port}`))
