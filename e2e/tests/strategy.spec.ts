import { API_URL, expect, test } from './fixtures'

test('test strategy: drafted from the project, written as prose, edited, saved as versions, exported', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Strategy')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { testing_mission: 'Find what stops a customer verifying with a passport' } })
  await api.risk(project.id, 'Forged IDs pass', 5, 4)

  await test.step('a first draft from what Mirmera knows', async () => {
    await page.goto(`/projects/${project.id}/dashboard`)
    await page.getByRole('navigation', { name: 'Project pages' }).getByRole('link', { name: 'Strategy' }).click()
    await expect(page.getByRole('textbox', { name: 'Mission' })).toHaveValue(/Testing mission: Find what stops a customer verifying with a passport/)
    await expect(page.getByRole('textbox', { name: 'What we won\'t test, and why' })).toHaveValue(/^Decide with the team what won't be tested and why/)
    const risks = page.getByRole('region', { name: 'Risks driving the testing' })
    await risks.getByRole('button', { name: /What Mirmera knows/ }).click()
    await expect(risks.getByRole('list')).toContainText('R1 Forged IDs pass (score 20, security): not explored')
    await expect(page.getByText('A draft, not saved yet')).toBeVisible()
  })

  await test.step('prose by AI, then edited and saved as version 1', async () => {
    await page.getByRole('button', { name: 'Write it as prose with AI' }).click()
    await expect(page.getByRole('note')).toContainText('Drafted by AI (mock')
    await expect(page.getByRole('textbox', { name: 'Mission' })).toHaveValue(/^\(mock\) Testing mission/)
    await page.getByRole('textbox', { name: 'Mission' }).fill('Protect passport verification: a customer must not be wrongly refused or wrongly accepted.')
    await page.getByRole('button', { name: 'Save as version 1' }).click()
    await expect(page.getByRole('status')).toHaveText('Saved as version 1.')
    await expect(page.getByLabel('Version')).toContainText('Version 1 (latest)')
  })

  await test.step('exports of the saved version', async () => {
    const versions = await (await request.get(`${API_URL}/projects/${project.id}/strategy`)).json()
    const md = await (await request.get(`${API_URL}/projects/${project.id}/strategy/${versions.versions[0].id}/export?format=md`)).text()
    expect(md).toContain('## Mission\n\nProtect passport verification')
    await expect(page.getByRole('link', { name: 'Markdown' })).toBeVisible()
  })

  await test.step('a change makes version 2; version 1 can still be opened', async () => {
    await page.getByRole('textbox', { name: 'What we won\'t test, and why' }).fill('Not the admin console this release: only staff use it, and it did not change.')
    await expect(page.getByText('Changed since version 1')).toBeVisible()
    await page.getByRole('button', { name: 'Save as version 2' }).click()
    await expect(page.getByRole('status')).toHaveText('Saved as version 2.')
    await page.getByLabel('Version').selectOption({ index: 1 })
    await expect(page.getByRole('textbox', { name: 'What we won\'t test, and why' })).not.toHaveValue(/admin console/)
    await expect(page.getByText('Showing version 1')).toBeVisible()
  })
})
