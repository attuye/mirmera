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
