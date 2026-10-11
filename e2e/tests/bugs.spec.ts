import { API_URL, expect, test } from './fixtures'

test('review bugs: draft with AI, enforce the oracle, save as bug or question', async ({ page, api }) => {
  const project = await api.project()
  const charter = await api.charter(project.id, 'Explore ID upload with expired and blurry documents')
  const session = await api.session(charter.id)

  await test.step('log notes and two bugs from the session screen', async () => {
    await page.goto(`/projects/${project.id}/sessions/${session.id}`)
    const box = page.getByPlaceholder('What are you observing?')
    for (const note of ['Uploaded an ID that expired in 2023', 'Clicked Verify', 'Verification passed with a green tick']) {
      await box.fill(note)
      await page.getByRole('button', { name: 'Log Note' }).click()
      await expect(page.getByText(note, { exact: true })).toBeVisible()
    }
    await page.getByRole('button', { name: 'Bug', exact: true }).click()
    for (const bug of ['Expired national ID is accepted', 'Should blurry selfies be rejected?']) {
      await box.fill(bug)
      await page.getByRole('button', { name: 'Log Bug' }).click()
      await expect(page.getByText(bug, { exact: true })).toBeVisible()
    }
  })

  const expired = page.getByRole('article', { name: 'Expired national ID is accepted' })
  const blurry = page.getByRole('article', { name: 'Should blurry selfies be rejected?' })

  await test.step('both wait for review on the Bugs page', async () => {
    await page.goto(`/projects/${project.id}/bugs`)
    await expect(expired.getByText('Needs review')).toBeVisible()
    await expect(blurry.getByText('Needs review')).toBeVisible()
  })

  await test.step('draft with AI: the form is pre-filled from the session notes', async () => {
    await expired.getByRole('button', { name: /Expired national ID/ }).click()
    await expect(expired.getByText('Verification passed with a green tick')).toBeVisible()
    await expired.getByRole('button', { name: 'Draft report with AI' }).click()
    await expect(expired.getByText(/Pre-filled from the AI draft/)).toBeVisible()
    await expect(expired.getByLabel(/Steps to reproduce/)).toHaveValue(/Clicked Verify/)
  })

  await test.step('a bug cannot be saved without an oracle', async () => {
    await expired.getByLabel(/^Oracle/).selectOption('')
    await expect(expired.getByText(/A bug needs an oracle and a reason/)).toBeVisible()
    await expect(expired.getByRole('button', { name: 'Save as bug' })).toBeDisabled()
  })

  await test.step('name the oracle and save as a bug', async () => {
    await expired.getByLabel(/^Oracle/).selectOption('statutes')
    await expired.getByLabel("Why it's a problem").fill('KYC regulation requires a valid, unexpired ID')
    await expired.getByLabel('Severity', { exact: true }).selectOption('high')
    await expired.getByLabel('Expected', { exact: true }).fill('Upload is rejected: the ID has expired')
    await expired.getByRole('button', { name: 'Save as bug' }).click()
    await expect(expired.getByText("Why it's a problem: statutes")).toBeVisible()
    await expect(expired.getByText(/Reviewed by tester as a bug/)).toBeVisible()
    await expect(expired.getByText('Bug', { exact: true })).toBeVisible()
  })

  await test.step('write the second one by hand as a question', async () => {
    await blurry.getByRole('button', { name: /Should blurry/ }).click()
    await blurry.getByRole('button', { name: 'Write it myself' }).click()
    await blurry.getByLabel(/Question: not sure/).check()
    await blurry.getByLabel(/Open questions/).fill('What blur level should be rejected?')
    await blurry.getByRole('button', { name: 'Save as question' }).click()
    await expect(blurry.getByText('What blur level should be rejected?')).toBeVisible()
  })

  await test.step('filters separate bugs from questions', async () => {
    await page.getByRole('tab', { name: /^Bugs/ }).click()
    await expect(expired).toBeVisible()
    await expect(blurry).toHaveCount(0)
    await page.getByRole('tab', { name: /^Questions/ }).click()
    await expect(blurry).toBeVisible()
    await expect(expired).toHaveCount(0)
  })

  await test.step('editing after a reload starts from the saved report, not the AI draft', async () => {
    await page.reload()
    await expired.getByRole('button', { name: /Expired national ID/ }).click()
    await expired.getByRole('button', { name: 'Edit report' }).click()
    await expect(expired.getByLabel('Expected', { exact: true })).toHaveValue('Upload is rejected: the ID has expired')
    await expect(expired.getByText(/An AI draft exists/)).toBeVisible()
  })
})

// The exploration agent met the same problem in other charters: one draft, with where else it showed up.
test('a bug the agent met again shows where, and how often it shows from its steps', async ({ page, api }) => {
  const project = await api.project()
  const charter = await api.charter(project.id, 'Explore identity verification with each document type')
  const session = await api.session(charter.id)
  await page.goto(`/projects/${project.id}/sessions/${session.id}`)
  await page.getByRole('button', { name: 'Bug', exact: true }).click()
  await page.getByPlaceholder('What are you observing?').fill('POST /api/verify fails with 500')
  await page.getByRole('button', { name: 'Log Bug' }).click()
  await expect(page.getByText('POST /api/verify fails with 500', { exact: true })).toBeVisible()

  const at = new Date().toISOString()
  await page.route(`${API_URL}/projects/${project.id}/bugs`, async (route) => {
    const response = await route.fetch().catch(() => null)
    if (!response) return
    const bugs = await response.json()
    bugs[0].sightings = [
      { session_id: 's2', charter_id: 'c2', mission: 'Explore expiry dates with a past date, today and a future date', at, url: '' },
      { session_id: 's3', charter_id: 'c3', mission: "Explore full names in Latin and Ge'ez script", at, url: '' },
    ]
    bugs[0].confirmation = { runs: 3, reproduced: 3, results: [1, 2, 3].map((run) => ({ run, reproduced: true, detail: 'seen' })) }
    await route.fulfill({ json: bugs })
  })
  await page.goto(`/projects/${project.id}/bugs`)
  const bug = page.getByRole('article', { name: 'POST /api/verify fails with 500' })
  await expect(bug.getByText('seen 3×')).toBeVisible()
  await bug.getByRole('button', { name: /POST \/api\/verify/ }).click()
  const seen = bug.getByRole('region', { name: 'Seen again' })
  await expect(seen).toContainText('Seen again 2 times')
  await expect(seen).toContainText('Explore expiry dates with a past date')
  await expect(seen).toContainText("Explore full names in Latin and Ge'ez script")
  await expect(bug.getByRole('region', { name: 'Replayed by Mirmera' })).toContainText('shown 3 of 3 (every time)')
  if (process.env.SHOTS) await bug.screenshot({ path: `${process.env.SHOTS}/seen-again.png` })
  await page.unrouteAll({ behavior: 'ignoreErrors' })  // a list refresh still in flight must not fail the test
})
