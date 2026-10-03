import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, type BrowserContext } from '@playwright/test'
import { API_URL, expect, test } from './fixtures'

const TARGET = 'http://localhost:5190/'
const EXTENSION = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'extension')

// Loads the real extension into its own Chromium (extensions need a persistent
// context, and the full Chromium in "new headless" mode), records the target
// app's tab into a session, then checks what Mirmera shows.
async function chromeWithExtension(): Promise<BrowserContext> {
  return chromium.launchPersistentContext('', {
    headless: true,
    ...(process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : { channel: 'chromium' }),
    args: [`--disable-extensions-except=${EXTENSION}`, `--load-extension=${EXTENSION}`],
  })
}

test('chrome extension: record a tab of your own browser into a session', async ({ page, api }) => {
  const project = await api.project()
  const charter = await api.charter(project.id, 'Explore document search in the everyday browser')
  const session = await api.session(charter.id)
  const chrome = await chromeWithExtension()

  try {
    let [worker] = chrome.serviceWorkers()
    worker ??= await chrome.waitForEvent('serviceworker')
    const extensionId = new URL(worker.url()).host

    const app = await chrome.newPage()
    await app.goto(TARGET)
    const tabId = await worker.evaluate(async () => (await chrome.tabs.query({ url: 'http://localhost:5190/*' }))[0]?.id)
    const popup = await chrome.newPage()
    await popup.goto(`chrome-extension://${extensionId}/popup.html?tabId=${tabId}`)

    await test.step('point the extension at this Mirmera and start recording the app tab', async () => {
      await popup.getByText('Settings', { exact: true }).click()
      await popup.getByLabel('Mirmera API').fill(API_URL)
      await popup.getByLabel('Mirmera app').fill('http://localhost:5180')
      await popup.getByRole('button', { name: 'Save settings' }).click()
      await popup.getByLabel('Session').selectOption(session.id)
      await popup.getByRole('button', { name: 'Record this tab' }).click()
      await expect(popup.getByRole('status')).toHaveText('Recording this tab.')
      await expect(popup.getByText('REC', { exact: true })).toBeVisible()
    })

    await test.step('test in the app: errors, a failing request, a search', async () => {
      await app.bringToFront()
      await app.reload()  // the page logs two console errors and its API call fails
      await app.getByLabel('Search documents').fill('passport')
      await app.getByLabel('Search documents').press('Enter')
      await expect(app).toHaveURL(/\?q=passport$/)
    })

    await test.step('log a bug from the popup: it gets a screenshot of the app tab', async () => {
      await popup.getByLabel('Bug').check()
      await popup.getByLabel('What are you observing?').fill('Search ignores the query')
      await popup.getByRole('button', { name: 'Log bug' }).click()
      await expect(popup.getByRole('status')).toHaveText('Bug logged, with a screenshot.')
    })

    await test.step('Mirmera shows the recording live', async () => {
      await page.goto(`/projects/${project.id}/sessions/${session.id}`)
      // A note logged from the popup while the session screen is open appears without a reload.
      await popup.getByLabel('Test').check()
      await popup.getByLabel('What are you observing?').fill('Searched for passport')
      await popup.getByRole('button', { name: 'Log note' }).click()
      await expect(page.getByText('Searched for passport', { exact: true })).toBeVisible({ timeout: 10_000 })
      const panel = page.getByRole('complementary', { name: 'Browser' })
      await expect(panel.getByRole('heading', { name: 'Recording from Chrome' })).toBeVisible()
      const reported = panel.getByRole('region', { name: 'What the page reported' })
      // Two errors per page load: the reload, then the search reloads the page again.
      await expect(reported.getByRole('listitem', { name: 'Upload widget failed to init' })).toContainText('×4')
      await expect(reported.getByRole('listitem', { name: `GET ${TARGET}api/documents → 500` })).toBeVisible()
      // The search's query value is not stored: only the parameter name.
      await expect(panel.getByRole('region', { name: 'Pages visited' })).toContainText('localhost:5190/?q=…')
      await expect(page.getByRole('img', { name: 'When logged: Search ignores the query' })).toBeVisible()
      await expect(page.getByText('Search ignores the query', { exact: true })).toBeVisible()
    })

    await test.step('your typing is recorded as actions, for tests and the agent', async () => {
      const actions = await (await page.request.get(`${API_URL}/sessions/${session.id}/evidence?kind=action`)).json()
      expect(actions.map((a: { summary: string }) => a.summary)).toEqual([
        'fill label "Search documents" = "passport"',
        'press Enter in label "Search documents"',
      ])
    })

    await test.step('stop: the session screen stops showing Chrome', async () => {
      await popup.getByRole('button', { name: 'Stop recording' }).click()
      await expect(popup.getByRole('status')).toHaveText('Stopped. The session keeps everything recorded.')
      await page.reload()
      const panel = page.getByRole('complementary', { name: 'Browser' })
      await expect(panel.getByRole('heading', { name: 'Recorded browser' })).toBeVisible()
    })
  } finally {
    await chrome.close()
  }
})
