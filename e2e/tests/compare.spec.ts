import { API_URL, expect, test } from './fixtures'

// Compare two versions: the session's journey (here sent as the Chrome extension records it) done
// again on two addresses. The target app plays "the next version" when reached as 127.0.0.1.
test('compare: the same steps on two environments show what changed, and a difference becomes a bug draft', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Compare')
  await request.patch(`${API_URL}/projects/${project.id}`, {
    data: { app_url: 'http://localhost:5190', environments: [{ name: 'next', url: 'http://127.0.0.1:5190' }] },
  })
  const charter = await api.charter(project.id, 'Explore document search on the next version')
  const session = await (await request.post(`${API_URL}/sessions`, { data: { charter_id: charter.id, tester_id: 'attu' } })).json()
  await request.post(`${API_URL}/sessions/${session.id}/extension/events`, { data: { events: [
    { kind: 'navigation', url: 'http://localhost:5190/' },
    { kind: 'action', url: 'http://localhost:5190/', action: { action: 'fill', locator: { method: 'label', value: 'Search documents' }, value: 'passport' } },
    { kind: 'action', url: 'http://localhost:5190/', action: { action: 'click', locator: { method: 'role', role: 'button', name: 'Search' } } },
  ] } })

  await page.goto(`/projects/${project.id}/sessions/${session.id}`)
  await page.getByRole('tab', { name: 'Compare' }).click()
  const panel = page.getByRole('region', { name: 'Compare environments' })
  await expect(panel.getByLabel('A address')).toHaveValue('http://localhost:5190')
  await expect(panel.getByLabel('B address')).toHaveValue('http://127.0.0.1:5190')
  await expect(panel.getByLabel('B browser')).toHaveValue('chromium')  // one address in two browsers works too
  await panel.getByRole('button', { name: 'Compare' }).click()

  const result = panel.getByRole('region', { name: 'Comparison result' })
  await expect(result.getByRole('status')).toHaveText('The same 3 steps on app and next: 1 step differs (step 1).', { timeout: 60_000 })
  await expect(result.getByRole('list', { name: 'Differences at step 1' })).toContainText('Only next has the heading "Upload your ID document"')
  await expect(result.getByRole('img', { name: 'Step 1 on next' })).toBeVisible()
  await page.screenshot({ path: 'test-results/compare.png', fullPage: true })

  await result.getByRole('button', { name: 'Bug on next' }).click()
  await expect(result).toContainText('1 bug draft from this comparison')
  await result.getByRole('link', { name: 'Review the bugs' }).click()
  await expect(page.getByText('On next, the page has the heading "Upload your ID document" after open /').first()).toBeVisible()
})
