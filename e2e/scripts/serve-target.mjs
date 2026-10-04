// A tiny stand-in for "the app under test" in the browser-runner tests. It
// misbehaves on purpose: console errors on load and an API call that fails.
import http from 'node:http'

const port = Number(process.env.E2E_TARGET_PORT ?? 5190)

const home = `<!doctype html><html lang="en"><title>KYC upload</title>
<style>body{font-family:sans-serif;padding:40px;background:#f6f7f9}</style>
<h1>Upload your ID</h1><p>Drop a photo of your national ID here.</p><div id="widget">Loading upload widget…</div>
<form action="/" method="get"><label for="q">Search documents</label> <input id="q" name="q"> <button>Search</button></form>
<script>
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

http
  .createServer((req, res) => {
    const path = new URL(req.url, 'http://localhost').pathname
    if (path === '/') {
      res.writeHead(200, { 'Content-Type': 'text/html' }).end(home)
    } else if (path === '/signup') {
      res.writeHead(200, { 'Content-Type': 'text/html' }).end(signup)
    } else if (path === '/api/slow') {
      setTimeout(() => res.writeHead(200, { 'Content-Type': 'application/json' }).end('{}'), 2300)
    } else if (path === '/api/documents') {
      res.writeHead(500, { 'Content-Type': 'application/json' }).end('{"error":"storage unavailable"}')
    } else {
      res.writeHead(404, { 'Content-Type': 'text/plain' }).end('not found')
    }
  })
  .listen(port, () => console.log(`Target app on http://localhost:${port}`))
