import { API_URL, expect, test } from './fixtures'

// The e2e backend runs with MOCK_TRACKERS=true: the pull request and issues come from the in-memory GitHub.
test('import artifacts: a pull request, past bugs, and a file into the paste box', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Imports')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { github_repo: 'addisverify/imports' } })
  await page.goto(`/projects/${project.id}/risks`)
  const artifacts = page.getByRole('group', { name: 'Artifacts to model' })

  await test.step('a pull request becomes a Diff, linked to where it came from', async () => {
    await page.getByRole('tab', { name: 'Import' }).click()
    const form = page.getByRole('group', { name: 'Import' })
    await expect(form).toContainText('Its title, description and diff, as a Diff.')
    await expect(form).toContainText('A number alone means addisverify/imports.')
    await form.getByLabel('Pull request').fill('12')
    await form.getByRole('button', { name: 'Import' }).click()
    const pr = page.getByText('PR #12: Check passports against the MRZ (mock PR 12)')
    await expect(pr).toBeVisible()
    await expect(page.getByRole('link', { name: 'imported ↗' })).toHaveAttribute('href', 'https://mock-github.local/addisverify/imports/pull/12')
  })

  await test.step('past bugs from GitHub issues become Bug history', async () => {
    await page.getByRole('button', { name: 'Add an artifact' }).click()
    await page.getByRole('tab', { name: 'Import' }).click()
    const form = page.getByRole('group', { name: 'Import' })
    await form.getByLabel('From').selectOption('github_issues')
    await expect(form.getByLabel('Labels')).toHaveValue('bug')
    await form.getByRole('button', { name: 'Import' }).click()
    await expect(page.getByText('GitHub issues: addisverify/imports (bug), 2')).toBeVisible()
  })

  await test.step('a problem says what to fix', async () => {
    await page.getByRole('button', { name: 'Add an artifact' }).click()
    await page.getByRole('tab', { name: 'Import' }).click()
    const form = page.getByRole('group', { name: 'Import' })
    await form.getByLabel('From').selectOption('jira_issues')
    await form.getByRole('button', { name: 'Import' }).click()
    await expect(form.getByRole('alert')).toContainText("Set the project's Jira project key in Settings first.")
  })

  await test.step('a file loads into the paste box, named after the file', async () => {
    await page.getByRole('tab', { name: 'Paste' }).click()
    await page.getByLabel('Load from file').setInputFiles({ name: 'mrz-check.patch', mimeType: 'text/plain', buffer: Buffer.from('+ check the MRZ\n') })
    await expect(page.getByLabel('Name')).toHaveValue('mrz-check.patch')
    await expect(page.getByLabel('Type')).toHaveValue('diff')
    await expect(page.getByLabel('Content')).toHaveValue('+ check the MRZ\n')
    await page.getByRole('button', { name: 'Add artifact' }).click()
    await expect(page.getByText('mrz-check.patch')).toBeVisible()
    await expect(artifacts.getByRole('checkbox')).toHaveCount(3)
  })
})
