import { expect, test } from './fixtures'

const TARGET = 'http://localhost:5190/'

// The target app fails GET /api/documents on every load. Nobody clicks in the
// backend's headless browser here, so the plan is "open the page, then assert
// the request succeeds"; recorded clicks and typing are covered by the backend
// tests with a real form.
test('regression test: generate from the recording, read the code, replay it to see it catch the bug', async ({ page, api, request }) => {
  const project = await api.project()
  const charter = await api.charter(project.id, 'Explore the document list after login')
  const session = await api.session(charter.id)
  const title = 'Documents fail to load on the upload page'

  await test.step('record the bug in the recorded browser', async () => {
    await page.goto(`/projects/${project.id}/sessions/${session.id}`)
    const panel = page.getByRole('complementary', { name: 'Browser' })
    await panel.getByLabel('App URL').fill(TARGET)
    await panel.getByRole('button', { name: 'Open browser' }).click()
    await expect(panel.getByRole('listitem', { name: `GET ${TARGET}api/documents → 500` })).toBeVisible()
    await page.getByRole('button', { name: 'Bug', exact: true }).click()
    await page.getByPlaceholder('What are you observing?').fill(title)
    await page.getByRole('button', { name: 'Log Bug' }).click()
    await expect(page.getByRole('img', { name: `When logged: ${title}` })).toBeVisible()
    const [bug] = (await api.sessionBugs(session.id)).filter((b) => b.title === title)
    await api.reviewBug(bug.id, {
      kind: 'bug', expected: 'The document list loads', actual: 'The list stays empty; the API answers 500',
      oracle_heuristic: 'purpose', oracle_explanation: 'The page exists to show the documents',
    })
  })

  const row = page.getByRole('article', { name: title })
  const panel = row.getByRole('region', { name: 'Regression test' })

  await test.step('generate: the code opens the page and asserts the request succeeds', async () => {
    await page.goto(`/projects/${project.id}/bugs`)
    await row.getByRole('button', { name: new RegExp(title) }).click()
    await panel.getByRole('button', { name: 'Generate regression test' }).click()
    const code = panel.getByLabel('Test code')
    await expect(code).toContainText('await page.goto(BASE_URL + "/")')
    await expect(code).toContainText('failedRequests.filter((r) => r.includes("/api/documents"))')
    await expect(code).toContainText("test.fail(true,")
    await expect(panel.getByLabel('Assumptions')).toContainText('Every recorded action was kept')
  })

  await test.step('the download is the same .spec.ts file', async () => {
    const href = await panel.getByRole('link', { name: 'Download' }).getAttribute('href')
    const file = await request.get(href!)
    expect(file.headers()['content-disposition']).toContain('documents-fail-to-load-on-the-upload-page.spec.ts')
    expect(await file.text()).toContain('import { test, expect } from \'@playwright/test\'')
  })

  await test.step('replay: the test catches the bug', async () => {
    await expect(panel.getByLabel('Replay against')).toHaveValue('http://localhost:5190')
    await panel.getByRole('button', { name: 'Replay test' }).click()
    const result = panel.getByRole('status', { name: 'Replay result' })
    await expect(result).toContainText('Reproduces the bug', { timeout: 30_000 })
    await expect(result).toContainText(`GET ${TARGET}api/documents -> 500`)
  })

  await test.step('replay against a server that is down: broken, and says which step', async () => {
    await panel.getByLabel('Replay against').fill('http://localhost:5199')
    await panel.getByRole('button', { name: 'Replay test' }).click()
    const result = panel.getByRole('status', { name: 'Replay result' })
    await expect(result).toContainText('Broken', { timeout: 30_000 })
    await expect(result).toContainText('failed1. open http://localhost:5199/')
  })
})
