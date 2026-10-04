import { API_URL, expect, test } from './fixtures'

test('trends: week by week, with a tooltip and a table view', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Trends')
  const charter = await api.charter(project.id, 'Explore renewals of expired IDs')
  const session = await api.session(charter.id)
  await api.note(session.id, 'test', 'Renewed an ID that expired yesterday')
  const bug = await api.bug(session.id, 'Renewal keeps the old expiry date')
  await api.reviewBug(bug.id, { kind: 'bug', severity: 'high', expected: 'New date', actual: 'Old date',
    oracle_heuristic: 'world', oracle_explanation: 'A renewed ID has a new expiry' })
  expect((await request.post(`${API_URL}/sessions/${session.id}/end`)).ok()).toBeTruthy()

  await page.goto(`/projects/${project.id}/dashboard`)
  await page.getByRole('navigation', { name: 'Project pages' }).getByRole('link', { name: 'Trends' }).click()
  await expect(page.getByRole('heading', { name: 'Trends', level: 1 })).toBeVisible()

  await test.step('six charts; this week has the session and the high bug', async () => {
    for (const name of ['Testing time', 'Bugs found', 'Bugs per hour of testing', 'Where the time went', 'Fixes checked after the issue closed', 'Risk exposure']) {
      await expect(page.getByRole('figure', { name })).toBeVisible()
    }
    const bugs = page.getByRole('figure', { name: 'Bugs found' })
    await bugs.locator('svg rect[fill="transparent"]').last().hover()
    const tip = bugs.getByRole('tooltip')
    await expect(tip).toContainText('High')
    await expect(tip).toContainText('1')
    const time = page.getByRole('figure', { name: 'Testing time' })
    await time.locator('svg rect[fill="transparent"]').last().hover()
    await expect(time.getByRole('tooltip')).toContainText('1 session · 1 tester')
  })

  await test.step('a table view, and the range', async () => {
    await page.getByRole('button', { name: 'Show as table' }).click()
    const table = page.getByRole('table', { name: 'Trends table' })
    await expect(table.getByRole('row')).toHaveCount(13)  // header + 12 weeks
    await expect(table.getByRole('row').last()).toContainText('1/0/0')  // this week: one high bug
    await page.getByRole('group', { name: 'Weeks' }).getByRole('button', { name: '8 weeks' }).click()
    await expect(table.getByRole('row')).toHaveCount(9)
  })
})
