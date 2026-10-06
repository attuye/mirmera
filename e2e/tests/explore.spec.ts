import { API_URL, expect, test } from './fixtures'

// The target app (scripts/serve-target.mjs) fails GET /api/documents on load: the
// exploration agent (mock AI) sees it, Mirmera checks it, and a bug draft is waiting.
test('exploration agent: Mirmera explores a charter and leaves a session and a bug draft to review', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Explore')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { app_url: 'http://localhost:5190' } })
  const story = await (await request.post(`${API_URL}/projects/${project.id}/stories`, { data: { title: 'Search uploaded documents', key: 'EXP-1' } })).json()
  const charter = await api.charter(project.id, 'Explore document search with odd names to discover what fails')
  await request.put(`${API_URL}/stories/${story.id}/charters`, { data: { ids: [charter.id] } })

  await test.step('let Mirmera explore the charter from the story', async () => {
    await page.goto(`/projects/${project.id}/stories/${story.id}`)
    const explore = page.getByRole('group', { name: 'Mirmera explores' })
    await explore.getByRole('button', { name: 'Let Mirmera explore' }).click()
    await expect(explore.getByRole('status')).toContainText('Explored', { timeout: 60_000 })
    await expect(explore.getByRole('status')).toContainText('1 bug draft to review')

    // The progress at the top of the story: stages, steps used, what it did last.
    const work = page.getByRole('region', { name: 'Mirmera at work' }).getByRole('article').first()
    await expect(work.getByRole('status')).toContainText('Done')
    await expect(work.getByRole('list', { name: 'Stages' })).toContainText('Explore in the app')
    await expect(work.getByRole('progressbar', { name: 'Steps used' })).toBeVisible()
    await expect(work.getByRole('list', { name: 'Latest steps' })).toContainText('problem: GET /api/documents fails with 500')
    await expect(work).toContainText('1 bug draft')
  })

  await test.step('the sidebar leads to Mirmera activity for the workspace', async () => {
    await page.getByRole('link', { name: 'Mirmera activity' }).click()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Mirmera activity')
    await expect(page.getByRole('region', { name: 'Recently' })).toContainText('Explore document search with odd names')
    await page.goBack()
  })

  await test.step('the bug draft waits for review; nothing was filed', async () => {
    await page.getByRole('group', { name: 'Mirmera explores' }).getByRole('link', { name: 'Review the bugs' }).click()
    await expect(page.getByText('GET /api/documents fails with 500').first()).toBeVisible()
  })

  await test.step('the session: the agent as tester, with its notes', async () => {
    await page.goto(`/projects/${project.id}/stories/${story.id}`)
    await expect(page.getByRole('region', { name: 'Sessions' })).toContainText('Mirmera agent')
    const left = page.getByRole('list', { name: "What's left" })
    await expect(left).toContainText('bug report to review')
    await expect(left).toContainText('Mirmera explored it')
    await left.getByRole('button', { name: 'Review session' }).click()
    await expect(page.getByText('GET /api/documents fails with 500').first()).toBeVisible()
    await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible()  // paused, waiting for a tester to end it
  })
})
