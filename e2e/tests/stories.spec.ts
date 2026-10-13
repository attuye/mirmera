import { API_URL, expect, test } from './fixtures'

test('stories: import one, add a risk, suggest charters for it, see what is left', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Stories')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { jira_project_key: 'STORY', default_tester: 'Attu' } })
  const issue = await (await request.post(`${API_URL}/mock-trackers/issues`, { data: {
    tracker: 'jira', target: 'STORY', title: 'As a customer I can upload my passport',
    body: 'Acceptance criteria:\n- JPG, PNG or PDF up to 10 MB\n- An expired passport is refused with a reason',
  } })).json()

  await test.step('import the story from Jira', async () => {
    await page.goto(`/projects/${project.id}/dashboard`)
    await page.getByRole('navigation', { name: 'Project pages' }).getByRole('link', { name: 'Stories' }).click()
    await page.getByLabel('Story key or address').fill(issue.issue_key)
    await page.getByRole('button', { name: 'Import story' }).click()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('As a customer I can upload my passport')
    await expect(page.getByRole('region', { name: 'What it claims' })).toContainText('An expired passport is refused with a reason')
    const left = page.getByRole('list', { name: "What's left" })
    await expect(left.getByRole('button')).toHaveText(['Link a risk', 'Add a charter'])
  })

  await test.step('a new risk for the story', async () => {
    const risks = page.getByRole('region', { name: 'Risks' })
    await risks.getByRole('button', { name: 'New risk' }).click()
    await risks.getByLabel('Risk', { exact: true }).fill('Expired passports pass')
    await risks.getByLabel('What fails, for whom, and when').fill('A customer with an expired passport is verified')
    await risks.getByRole('button', { name: 'Add risk to this story' }).click()
    await expect(risks.getByRole('article', { name: 'R1 Expired passports pass' })).toContainText('No charter here investigates it')
    await expect(page.getByRole('list', { name: "What's left" })).toContainText('R1 Expired passports pass: no charter in this story investigates it.')
  })

  await test.step('suggest charters for the story and accept one', async () => {
    await page.getByRole('region', { name: 'Charters' }).getByRole('link', { name: 'Suggest charters' }).click()
    await expect(page.getByRole('note', { name: 'For a story' })).toContainText(`${issue.issue_key}: As a customer I can upload my passport`)
    const first = page.getByRole('region', { name: 'Suggestions' }).getByRole('article').first()
    const mission = await first.getByRole('button', { name: 'Accept' }).evaluate((b) => b.closest('article')?.getAttribute('aria-label') ?? '')
    await first.getByRole('button', { name: 'Accept' }).click()
    const charter = page.getByRole('region', { name: 'Your charters' }).getByRole('listitem', { name: mission })
    await expect(charter.getByRole('link', { name: `${issue.issue_key}: As a customer I can upload my passport`, exact: true })).toBeVisible()
    await page.getByRole('link', { name: 'Back to the story →' }).click()
    await expect(page.getByRole('region', { name: 'Charters' }).getByRole('article', { name: mission })).toContainText('not explored yet')
    await expect(page.getByRole('list', { name: "What's left" })).toContainText(`Not explored yet: ${mission}`)
  })

  await test.step('the risk is linked on the Risks page too, and the list counts it', async () => {
    await page.getByRole('navigation', { name: 'Project pages' }).getByRole('link', { name: 'Risks' }).click()
    await expect(page.getByRole('article', { name: 'R1 Expired passports pass' }).getByRole('link', { name: /As a customer I can upload/ })).toBeVisible()
    await page.getByRole('navigation', { name: 'Project pages' }).getByRole('link', { name: 'Stories' }).click()
    const row = page.getByRole('region', { name: 'Your stories' }).getByRole('listitem', { name: 'As a customer I can upload my passport' })
    await expect(row).toContainText('1 risk')
    await expect(row).toContainText('1 charter')
  })

  await test.step('a story written here, marked done', async () => {
    await page.getByRole('tab', { name: 'Write one' }).click()
    await page.getByLabel('Story', { exact: true }).fill('Customers can retry a failed upload')
    await page.getByRole('button', { name: 'Add story' }).click()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Customers can retry a failed upload')
    await page.getByRole('button', { name: 'Mark done' }).click()
    await expect(page.getByRole('button', { name: 'Reopen' })).toBeVisible()
    await page.getByRole('link', { name: 'Stories', exact: true }).first().click()
    await page.getByRole('group', { name: 'Show' }).getByRole('button', { name: /^Done/ }).click()
    await expect(page.getByRole('region', { name: 'Your stories' }).getByRole('listitem')).toHaveText([/Customers can retry a failed upload/])
  })
})

test('stories: a Jira story comes with its sprint', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Jira Sprints')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { jira_project_key: 'SPR' } })
  const add = async (title: string, sprints: object[] | null) => (await (await request.post(`${API_URL}/mock-trackers/issues`, { data: {
    tracker: 'jira', target: 'SPR', title, body: 'As a customer…', sprints } })).json()).issue_key as string
  const sprint14 = { id: 914, name: 'Sprint 14', state: 'active', startDate: '2026-09-28T06:00:00.000Z', endDate: '2026-10-11T15:00:00.000Z', goal: 'Passports end to end' }
  const first = await add('Upload a passport', [{ id: 913, name: 'Sprint 13', state: 'closed', startDate: '2026-09-14', endDate: '2026-09-27' }, sprint14])
  const second = await add('Pay in Birr', [{ id: 915, name: 'Sprint 15', state: 'future' }])

  await test.step('import: the story lands in Jira\'s active sprint, made here with its dates', async () => {
    await page.goto(`/projects/${project.id}/stories`)
    await page.getByLabel('Story key or address').fill(first)
    await page.getByRole('button', { name: 'Import story' }).click()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Upload a passport')
    await expect(page.getByRole('status', { name: 'Sprint from Jira' })).toHaveText('In Sprint 14, as in Jira: a new sprint here, 28 Sep – 11 Oct 2026.')
    await expect(page.getByRole('combobox', { name: 'Sprint' })).toHaveValue(/.+/)
    await expect(page.getByRole('combobox', { name: 'Sprint' }).locator('option:checked')).toHaveText('Sprint 14')
    await page.screenshot({ path: 'test-results/jira-sprint-import.png' })
  })

  await test.step('a sprint Jira has not dated yet: said, and the story stays out of sprints', async () => {
    await page.goto(`/projects/${project.id}/stories`)
    await page.getByLabel('Story key or address').fill(second)
    await page.getByRole('button', { name: 'Import story' }).click()
    await expect(page.getByRole('status', { name: 'Sprint from Jira' })).toContainText("In Jira it's in Sprint 15, which has no dates in Jira yet")
    await expect(page.getByRole('combobox', { name: 'Sprint' })).toHaveValue('')
  })

  await test.step('the list and the sprint page show it', async () => {
    await page.getByRole('navigation', { name: 'Project pages' }).getByRole('link', { name: 'Stories' }).click()
    const row = page.getByRole('region', { name: 'Your stories' }).getByRole('listitem', { name: 'Upload a passport' })
    await row.getByRole('link', { name: 'Sprint 14' }).click()
    await expect(page.getByRole('heading', { level: 1 })).toContainText('Sprint 14')
    await expect(page.getByText('Passports end to end')).toBeVisible()
    await expect(page.getByText('Upload a passport')).toBeVisible()
    await page.screenshot({ path: 'test-results/jira-sprint-page.png' })
  })
})

test('stories: suggest risks from a story, link a touched one, accept and drop new ones', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Story Risks')
  const risk = async (title: string) => (await (await request.post(`${API_URL}/projects/${project.id}/risks`, { data: {
    title, failure_mode: `${title}, for customers`, quality_criterion: 'capability', impact: 4, likelihood: 3 } })).json())
  const expired = await risk('Expired passports accepted')
  await risk('Slow dashboard on Mondays')
  const story = await (await request.post(`${API_URL}/projects/${project.id}/stories`, { data: {
    title: 'Upload a passport',
    description: 'As a customer I can upload my passport.\nAcceptance criteria:\n- JPG, PNG or PDF up to 10 MB\n- An expired passport is refused with a reason' } })).json()

  await page.goto(`/projects/${project.id}/stories/${story.id}`)
  const risks = page.getByRole('region', { name: 'Risks' })

  await test.step('suggest: a touched project risk, and new risks added as proposed', async () => {
    await risks.getByRole('button', { name: 'Suggest risks' }).click()
    const panel = page.getByRole('region', { name: 'Suggested risks' })
    await expect(panel).toContainText('Added 3 new risks below as proposed (R3, R4, R5)')
    const touched = panel.getByRole('list', { name: 'Project risks this story touches' }).getByRole('listitem')
    await expect(touched).toHaveCount(1)
    await expect(touched.first()).toContainText(`${expired.ref} · 12`)
    await expect(touched.first()).toContainText('Expired passports accepted')
    await expect(risks.getByRole('article')).toHaveCount(3)
    await expect(risks.getByRole('article', { name: 'R3 Upload a passport: valid input refused' })).toContainText('(proposed)')
    await page.screenshot({ path: 'test-results/story-risks-suggested.png' })
  })

  await test.step('link the touched risk; accept one new risk and drop another', async () => {
    await page.getByRole('button', { name: `Link ${expired.ref}` }).click()
    await expect(risks.getByRole('article', { name: `${expired.ref} Expired passports accepted` })).toBeVisible()
    await expect(page.getByRole('list', { name: 'Project risks this story touches' })).toHaveCount(0)
    await page.getByRole('button', { name: 'Accept R3' }).click()
    await expect(risks.getByRole('article', { name: 'R3 Upload a passport: valid input refused' })).not.toContainText('(proposed)')
    await page.getByRole('button', { name: 'Drop R5' }).click()
    await expect(risks.getByRole('article', { name: /^R5 / })).toHaveCount(0)
    await expect(risks.getByRole('article')).toHaveCount(3)  // R1, R3, R4
  })

  await test.step('after a reload the suggestions are remembered', async () => {
    await page.reload()
    await expect(page.getByRole('region', { name: 'Suggested risks' })).toContainText('Added 3 new risks')
    await expect(page.getByRole('list', { name: 'Project risks this story touches' })).toHaveCount(0)
    await page.screenshot({ path: 'test-results/story-risks-reviewed.png' })
  })
})

test('stories: a story with no description can\'t be analysed', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Thin Story')
  const story = await (await request.post(`${API_URL}/projects/${project.id}/stories`, { data: { title: 'Pay in Birr' } })).json()
  await page.goto(`/projects/${project.id}/stories/${story.id}`)
  await page.getByRole('region', { name: 'Risks' }).getByRole('button', { name: 'Suggest risks' }).click()
  await expect(page.getByRole('region', { name: 'Risks' })).toContainText('The story has no description to find risks in.')
})
