// Starts a throwaway backend for the browser tests: its own port, a fresh
// SQLite database, and the mock AI provider. Your real mirmera.db is never touched.
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const backend = path.resolve(here, '..', '..', 'backend')
const dataDir = path.resolve(here, '..', '.e2e-data')
const dbFile = path.join(dataDir, 'e2e.db')
const evidenceDir = path.join(dataDir, 'evidence')

const port = process.env.E2E_API_PORT ?? '8100'
const webUrl = process.env.E2E_WEB_URL ?? 'http://localhost:5180'

// Prefer the backend's own virtualenv when there is one (Windows or Unix layout).
const venvPython = [
  path.join(backend, '.venv', 'Scripts', 'python.exe'),
  path.join(backend, '.venv', 'bin', 'python'),
].find(existsSync)
const python = process.env.PYTHON ?? venvPython ?? 'python'

mkdirSync(dataDir, { recursive: true })
rmSync(dbFile, { force: true })
rmSync(evidenceDir, { recursive: true, force: true })

const child = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--port', port], {
  cwd: backend,
  stdio: 'inherit',
  env: {
    ...process.env,
    // Forward slashes keep the SQLite URL valid on Windows too.
    DATABASE_URL: `sqlite:///${dbFile.replace(/\\/g, '/')}`,
    DEFAULT_AI_PROVIDER: 'mock',
    CORS_ORIGINS: webUrl,
    // The session runner's browser runs headless in tests; nobody is there to look at it.
    RUNNER_HEADLESS: 'true',
    EVIDENCE_DIR: evidenceDir,
    // In-memory GitHub and Jira: the tests never touch a real tracker.
    MOCK_TRACKERS: 'true',
    MOCK_NOTIFICATIONS: 'true',
    ...(process.env.PW_CHROMIUM_PATH ? { RUNNER_CHROMIUM_PATH: process.env.PW_CHROMIUM_PATH } : {}),
  },
})

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => child.kill(signal))
}
child.on('exit', (code) => process.exit(code ?? 0))
