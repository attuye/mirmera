import { test, expect } from './fixtures'

test('projects: create one, set it up, work inside it, switch away', async ({ page, api }) => {
  const shared = await api.project()
  const name = `Ethio Telebirr ${Date.now()}`
  let projectUrl = ''

  await test.step('create a project from the Projects page', async () => {
    await page.goto('/projects')
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    await page.getByRole('button', { name: '+ New project' }).click()
    const form = page.getByRole('form', { name: 'New project' })
    await form.getByLabel('Name').fill(name)
    await form.getByLabel('Description (optional)').fill('Mobile money wallet')
    await form.getByLabel('App URL (optional)').fill('http://localhost:5190')
    await form.getByRole('button', { name: 'Create project' }).click()

    await expect(page).toHaveURL(/\/projects\/[^/]+\/dashboard$/)
    await expect(page.getByRole('heading', { name })).toBeVisible()
    await expect(page.getByText('Mobile money wallet')).toBeVisible()
    const nudge = page.getByRole('note', { name: 'Finish setting up' })
    await expect(nudge).toHaveText(/add a default tester, and any other environments\./)
    await nudge.getByRole('link', { name: 'Open Settings →' }).click()
    await expect(page).toHaveURL(/\/settings$/)
    await expect(page.getByRole('combobox', { name: 'Project' })).toHaveValue(/.+/)
    projectUrl = page.url().replace(/\/(dashboard|settings)$/, '')
  })

  await test.step('settings: environments and a default tester', async () => {
    await expect(page.getByLabel('App URL (default)')).toHaveValue('http://localhost:5190')
    await page.getByRole('button', { name: '+ Add environment' }).click()
    await page.getByLabel('Environment 1 name').fill('staging')
    await page.getByLabel('Environment 1 URL').fill('localhost:5190/staging')
    await page.getByLabel('Default tester').fill('Attu')
    await page.getByRole('button', { name: 'Save settings' }).click()
    await expect(page.getByRole('alert')).toHaveText('Addresses must start with http:// or https://.')

    await page.getByLabel('Environment 1 URL').fill('http://localhost:5190/staging')
    await page.getByRole('button', { name: 'Save settings' }).click()
    await expect(page.getByRole('status')).toHaveText('Saved.')
    await page.reload()
    await expect(page.getByLabel('Environment 1 name')).toHaveValue('staging')
    await expect(page.getByLabel('Default tester')).toHaveValue('Attu')
  })

  const projectId = projectUrl.split('/projects/')[1]
  await api.charter(projectId, 'Explore sending money to a wrong number')

  await test.step('start a session as the default tester; the browser panel knows the addresses', async () => {
    await page.getByRole('link', { name: 'Dashboard', exact: true }).click()
    await expect(page.getByLabel('Tester name')).toHaveValue('Attu')
    await expect(page.getByRole('note', { name: 'Finish setting up' })).toHaveCount(0)
    await page.getByText('Explore sending money to a wrong number').click()
    await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/sessions/[^/]+$`))

    const browser = page.getByRole('complementary', { name: 'Browser' })
    await expect(browser.getByLabel('App URL')).toHaveValue('http://localhost:5190')
    const envs = browser.getByRole('group', { name: 'Environments' })
    await envs.getByRole('button', { name: 'staging' }).click()
    await expect(browser.getByLabel('App URL')).toHaveValue('http://localhost:5190/staging')
    await expect(envs.getByRole('button', { name: 'staging' })).toHaveAttribute('aria-pressed', 'true')
  })

  await test.step('an old session link finds its project', async () => {
    const sessionId = page.url().split('/sessions/')[1]
    const session = await (await page.request.get(`http://localhost:8100/sessions/${sessionId}`)).json()
    expect(session.tester_id).toBe('Attu')
    await page.goto(`/sessions/${sessionId}`)
    await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/sessions/${sessionId}$`))
  })

  await test.step('each project sees only its own work; the switcher keeps the page', async () => {
    await page.goto(`${projectUrl}/bugs`)
    await page.getByRole('combobox', { name: 'Project' }).selectOption(shared.id)
    await expect(page).toHaveURL(new RegExp(`/projects/${shared.id}/bugs$`))
    await page.getByRole('link', { name: 'Dashboard', exact: true }).click()
    await expect(page.getByRole('heading', { name: shared.name })).toBeVisible()
    await expect(page.getByText('Explore sending money to a wrong number')).toHaveCount(0)
  })

  await test.step('the menu groups pages; a group folds away and stays folded', async () => {
    const nav = page.getByRole('navigation', { name: 'Project pages' })
    await page.goto(`/projects/${shared.id}/coverage`)
    await expect(nav.getByRole('group', { name: 'Insights' }).getByRole('link', { name: 'Coverage' })).toHaveAttribute('aria-current', 'page')
    await expect(nav.getByRole('group', { name: 'Plan' }).getByRole('link')).toHaveText(['Sprints', 'Stories', 'Risks', 'Charters', 'Heuristics'])
    await expect(nav.getByRole('group', { name: 'Findings' }).getByRole('link')).toHaveText(['Bugs', 'Regression'])
    const insights = nav.getByRole('button', { name: 'Insights' })
    await insights.click()
    await expect(insights).toHaveAttribute('aria-expanded', 'false')
    await expect(nav.getByRole('link', { name: 'Report' })).toHaveCount(0)
    await page.reload()
    await expect(nav.getByRole('link', { name: 'Report' })).toHaveCount(0)  // remembered in this browser
    await insights.click()
    await expect(nav.getByRole('link', { name: 'Report' })).toBeVisible()
  })

  await test.step('the Projects page counts the work and "/" returns to the last project', async () => {
    await page.goto('/projects')
    const card = page.getByRole('link', { name })
    await expect(card).toContainText('1 charter')
    await expect(card).toContainText('1 session')
    await expect(card).toContainText('Last session just now')
    await page.goto('/')
    await expect(page).toHaveURL(new RegExp(`/projects/${shared.id}/dashboard$`))
  })
})
