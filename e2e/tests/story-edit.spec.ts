import { API_URL, expect, test } from './fixtures'

// Edit a story by hand: its title, key, link and acceptance criteria. Mirmera reads the rules fresh each run.
test('stories: edit a story and add an acceptance criterion', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Story Edit')
  const story = await (await request.post(`${API_URL}/projects/${project.id}/stories`, { data: {
    title: 'Verify my identity', key: 'KYC-1',
    description: 'Acceptance criteria\n- Expired documents are rejected.',
  } })).json()
  await page.goto(`/projects/${project.id}/stories/${story.id}`)
  await page.getByRole('button', { name: 'Edit story' }).click()
  const form = page.getByRole('form', { name: 'Edit story' })
  await expect(form.getByRole('button', { name: 'Save story' })).toBeDisabled()  // nothing changed yet
  await form.getByLabel('Title').fill('Verify my identity and see the result')
  await form.getByLabel('Key').fill('KYC-7')
  const criteria = form.getByLabel('Description and acceptance criteria')
  await criteria.fill(`${await criteria.inputValue()}\n- Signing out removes the customer's session and details.`)
  if (process.env.SHOTS) await form.screenshot({ path: `${process.env.SHOTS}/story-edit.png` })
  await form.getByRole('button', { name: 'Save story' }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Verify my identity and see the result')
  await expect(page.getByText('KYC-7', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'What it claims' })).toContainText("Signing out removes the customer's session and details.")
  await page.reload()  // saved, not just shown
  await expect(page.getByRole('region', { name: 'What it claims' })).toContainText('Expired documents are rejected.')
  await expect(page.getByRole('region', { name: 'What it claims' })).toContainText("Signing out removes the customer's session and details.")

  await page.getByRole('button', { name: 'Edit story' }).click()  // Cancel leaves it as it was
  await page.getByLabel('Title').fill('Something else')
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Verify my identity and see the result')
})
