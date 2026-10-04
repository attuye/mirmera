import { API_URL, expect, test } from './fixtures'

const TARGET = 'http://localhost:5190/'

// The target app fails GET /api/documents on every load. Nobody clicks in the
// backend's headless browser here, so the plan is "open the page, then assert
// the request succeeds"; recorded clicks and typing are covered by the backend
// tests with a real form.
test('regression test: generate from the recording, read the code, replay it to see it catch the bug', async ({ page, api, request }) => {
  test.setTimeout(150_000)  // several replays in a real browser
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

  await test.step('edit the steps: add a search, save, and the test replays exactly those', async () => {
    const steps = row.getByRole('region', { name: 'Steps to the bug' })
    await expect(steps).toContainText('The AI picks from these')  // no request failed after an action: Mirmera can't anchor
    await steps.getByRole('button', { name: /Steps to the bug/ }).click()
    await expect(steps.getByRole('listitem')).toHaveCount(1)

    await steps.getByRole('button', { name: '+ Add step' }).click()
    let form = steps.getByRole('form', { name: 'New step' })
    await form.getByRole('combobox', { name: 'Step', exact: true }).selectOption('fill')
    await form.getByLabel('Find it by').selectOption('label')
    await form.getByLabel('Which one').fill('Search documents')
    await form.getByLabel('Text to type').fill('passport')
    await form.getByRole('button', { name: 'Add step' }).click()

    await steps.getByRole('button', { name: '+ Add step' }).click()
    form = steps.getByRole('form', { name: 'New step' })
    await form.getByLabel('Name', { exact: true }).fill('Search')  // click a button named Search: the defaults
    await form.getByRole('button', { name: 'Add step' }).click()
    await expect(steps.getByRole('listitem', { name: 'fill label "Search documents" = "passport"' })).toBeVisible()
    await expect(steps).toContainText('Unsaved changes')

    await steps.getByRole('button', { name: 'Save steps' }).click()
    await expect(steps.getByRole('status')).toContainText('Generate the regression test again')
    await expect(steps).toContainText('Edited by you')

    await panel.getByRole('button', { name: 'Generate again' }).click()
    const code = panel.getByLabel('Test code')
    await expect(code).toContainText('await page.getByLabel("Search documents", { exact: true }).fill("passport")')
    await expect(code).toContainText('await page.getByRole("button", { name: "Search", exact: true }).click()')
    await panel.getByRole('button', { name: 'Replay test' }).click()
    await expect(panel.getByRole('status', { name: 'Replay result' })).toContainText('Reproduces the bug', { timeout: 30_000 })

    // Isolate: vary the search text; the documents request fails whatever is typed.
    const iso = row.getByRole('region', { name: 'Isolate the bug' })
    await iso.getByLabel('What shows the bug').selectOption('failed_request')
    await iso.getByLabel('Request URL contains').fill('/api/documents')
    await iso.getByRole('button', { name: 'Isolate the bug' }).click()
    const isolation = iso.getByRole('status', { name: 'Isolation result' })
    await expect(isolation).toContainText('Done', { timeout: 30_000 })
    await expect(isolation.getByRole('row', { name: 'label "Search documents": left empty instead of "passport"' })).toContainText('Still fails')
    await expect(isolation.getByLabel('What the bug depends on')).toContainText('No single change made it go away')
    await expect(isolation.getByLabel('What the bug depends on')).toContainText('Didn\'t matter: label "Search documents"')

    await steps.getByRole('button', { name: 'Go back to the recording' }).click()
    await expect(steps).toContainText('The AI picks from these')
    await expect(steps.getByRole('listitem')).toHaveCount(1)
  })

  await test.step('replay against a server that is down: broken, and says which step', async () => {
    await panel.getByLabel('Replay against').fill('http://localhost:5199')
    await panel.getByRole('button', { name: 'Replay test' }).click()
    const result = panel.getByRole('status', { name: 'Replay result' })
    await expect(result).toContainText('Broken', { timeout: 30_000 })
    await expect(result).toContainText('failed1. open http://localhost:5199/')
  })

  await test.step('the Regression page runs every test and exports them as a Playwright project', async () => {
    await page.getByRole('link', { name: 'Regression', exact: true }).click()
    await page.getByLabel('Replay against').fill('')  // each test's recorded address
    await page.getByRole('button', { name: /^Run all/ }).click()
    const latest = page.getByRole('region', { name: 'Latest run' })
    await expect(latest.getByRole('status')).toContainText('Latest run', { timeout: 60_000 })
    await expect(latest.getByRole('row', { name: title })).toContainText('Bug still there')
    await expect(latest.getByRole('row', { name: title })).toContainText('expected: marked as a known bug')
    await expect(page.getByRole('region', { name: 'Tests' }).getByRole('row', { name: title })).toContainText('Bug still there')
    await expect(page.getByRole('region', { name: 'History' }).getByRole('listitem')).toHaveCount(1)

    const href = await page.getByRole('link', { name: 'Export as Playwright project' }).getAttribute('href')
    const zip = await request.get(href!)
    expect(zip.headers()['content-type']).toBe('application/zip')
    expect((await zip.body()).subarray(0, 2).toString()).toBe('PK')  // a zip file
  })

  await test.step('retest when the issue closes: the bug is still there, and the issue says so', async () => {
    // File the bug to the mock GitHub under its own repo, so the trackers spec's repo stays untouched.
    const [bug] = (await api.sessionBugs(session.id)).filter((b) => b.title === title)
    const patch = (github_repo: string) => request.patch(`${API_URL}/projects/${project.id}`, { data: { github_repo } })
    expect((await patch('addisverify/retest')).ok()).toBeTruthy()
    const filed = await request.post(`${API_URL}/bugs/${bug.id}/file`, { data: { tracker: 'github' } })
    expect(filed.ok(), await filed.text()).toBeTruthy()
    const key = (await filed.json()).filing.issue_key
    expect((await patch('')).ok()).toBeTruthy()  // the filing keeps its own repo

    await page.reload()
    const closed = page.getByRole('region', { name: 'Closed issues' })
    await closed.getByRole('button', { name: 'Check closed issues' }).click()
    await expect(closed.getByRole('status')).toContainText('Checked 1 of 1 filed issue: none newly closed.')

    const state = await request.post(`${API_URL}/mock-trackers/issue-state`,
      { data: { tracker: 'github', target: 'addisverify/retest', issue_key: key, closed: true } })
    expect(state.ok()).toBeTruthy()
    await closed.getByRole('button', { name: 'Check closed issues' }).click()
    await expect(closed.getByRole('status')).toContainText('1 newly closed, retesting.')
    const retest = closed.getByRole('row', { name: `Retest ${key}` })
    await expect(retest).toContainText('Still fails', { timeout: 30_000 })
    await expect(retest).toContainText(`Result commented on ${key}`)
    await expect(retest).toContainText('The issue may need reopening')

    await closed.getByRole('button', { name: 'Check closed issues' }).click()  // one retest per close
    await expect(closed.getByRole('status')).toContainText('none newly closed.')
    await expect(closed.getByRole('row')).toHaveCount(1)
  })
})
