import { expect, test } from './fixtures'

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
