import { API_URL, expect, test } from './fixtures'

test('sprints: plan stories, see all testing in the dates, open the report and coverage for the sprint', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Sprints')
  const post = async (url: string, data: object) => (await request.post(`${API_URL}${url}`, { data })).json()
  const upload = await post(`/projects/${project.id}/stories`, { title: 'Upload a passport', key: 'KYC-12' })
  const retry = await post(`/projects/${project.id}/stories`, { title: 'Retry a failed upload', key: 'KYC-13' })
  const inside = await post(`/projects/${project.id}/charters`, { mission_statement: 'Explore passport upload', story_ids: [upload.id] })
  const outside = await api.charter(project.id, 'Hunt for slow pages')
  for (const c of [inside, outside]) {
    const s = await api.session(c.id)
    await request.post(`${API_URL}/sessions/${s.id}/end`)
  }

  await test.step('a new sprint with one story', async () => {
    await page.goto(`/projects/${project.id}/dashboard`)
    await page.getByRole('navigation', { name: 'Project pages' }).getByRole('link', { name: 'Sprints' }).click()
    const form = page.getByRole('region', { name: 'New sprint' })
    await expect(form.getByLabel('Name')).toHaveValue('Sprint 1')
    await form.getByLabel('Goal (optional)').fill('Passports end to end')
    await form.getByRole('checkbox', { name: /Upload a passport/ }).check()
    await form.getByRole('button', { name: 'Add sprint' }).click()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Sprint 1')
    await expect(page.getByText('Current · 14 days left')).toBeVisible()
  })

  await test.step('its testing: under its stories and outside them', async () => {
    const split = page.getByRole('group', { name: 'Where the time went' })
    await expect(split).toContainText("Under the sprint's stories1 session")
    await expect(split).toContainText('Outside them1 session')
    await expect(page.getByRole('list', { name: "Testing outside the sprint's stories" })).toContainText('Hunt for slow pages')
    await expect(page.getByRole('list', { name: "What's left" })).toContainText('KYC-12: No risks linked yet')
  })

  await test.step('add and take out a story', async () => {
    const stories = page.getByRole('region', { name: 'Stories' })
    await stories.getByLabel('Add a story to this sprint').selectOption({ label: 'KYC-13: Retry a failed upload' })
    await stories.getByRole('button', { name: 'Add', exact: true }).click()
    await expect(stories.getByRole('article')).toHaveCount(2)
    await stories.getByRole('button', { name: 'Take KYC-13 out of the sprint' }).click()
    await expect(stories.getByRole('article')).toHaveCount(1)
  })

  await test.step('the report and the coverage map for the sprint', async () => {
    await page.getByRole('link', { name: 'Report', exact: true }).last().click()
    await expect(page.getByRole('group', { name: 'Period' }).getByLabel('Sprint')).toHaveValue(/.+/)
    await expect(page.getByRole('region', { name: 'Totals' })).toContainText('Sessions2')
    await page.goBack()
    await page.getByRole('main').getByRole('link', { name: 'Coverage' }).click()
    await expect(page.getByRole('group', { name: 'Period' }).getByLabel('Sprint')).toHaveValue(/.+/)
  })

  await test.step('stories: filter by sprint, and move a story from its page', async () => {
    await page.getByRole('navigation', { name: 'Project pages' }).getByRole('link', { name: 'Stories' }).click()
    const list = page.getByRole('region', { name: 'Your stories' })
    await list.getByLabel('Sprint').selectOption({ label: 'Sprint 1 (current)' })
    await expect(list.getByRole('listitem')).toHaveText([/Upload a passport.*Sprint 1/])
    await list.getByLabel('Sprint').selectOption({ label: 'No sprint' })
    await list.getByRole('link', { name: 'Retry a failed upload' }).click()
    await page.getByRole('group', { name: 'Totals' }).getByLabel('Sprint').selectOption({ label: 'Sprint 1 (current)' })
    await expect(page.getByRole('group', { name: 'Totals' }).getByLabel('Sprint')).toHaveValue(/.+/)
    expect((await (await request.get(`${API_URL}/stories/${retry.id}`)).json()).sprint.name).toBe('Sprint 1')
  })
})
