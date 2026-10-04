import { defineConfig, devices } from '@playwright/test'

// The tests run against their own servers on their own ports, so they never
// touch your dev servers or your real database.
const API_PORT = 8100
const WEB_PORT = 5180
const TARGET_PORT = 5190 // the misbehaving "app under test" for the browser-runner spec
export const API_URL = `http://localhost:${API_PORT}`
const WEB_URL = `http://localhost:${WEB_PORT}`

export default defineConfig({
  testDir: './tests',
  globalSetup: './scripts/sign-in.ts',  // one account for every spec; see scripts/sign-in.ts
  // One worker, in order: the specs share one fresh database and one product.
  // Each spec owns its own area (risks, bugs, session notes).
  workers: 1,
  fullyParallel: false,
  // No retries: a test that only passes on retry is a flaky product or a flaky
  // test, and either way we want to see it.
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI
    ? [['github'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: WEB_URL,
    storageState: '.e2e-data/signed-in.json',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    viewport: { width: 1440, height: 1000 },
    // Lets an environment with a preinstalled Chromium use it instead of downloading one.
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } } }],
  webServer: [
    {
      command: 'node scripts/serve-target.mjs',
      url: `http://localhost:${TARGET_PORT}`,
      env: { E2E_TARGET_PORT: String(TARGET_PORT) },
      reuseExistingServer: false,
      timeout: 15_000,
    },
    {
      command: 'node scripts/start-backend.mjs',
      url: `${API_URL}/health`,
      env: { E2E_API_PORT: String(API_PORT), E2E_WEB_URL: WEB_URL },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --port ${WEB_PORT} --strictPort`,
      cwd: '../frontend',
      url: WEB_URL,
      env: { VITE_API_URL: API_URL },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
