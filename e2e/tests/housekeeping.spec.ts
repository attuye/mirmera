import { test, expect, API_URL } from './fixtures'

test('housekeeping: delete a bug and a session, archive and delete a project', async ({ page, api, request }) => {
  const name = `Scratch project ${Date.now()}`
  const project = await api.newProject(name)
  const charter = await api.charter(project.id, 'Explore something to throw away')
  const session = await api.session(charter.id)
  await api.bug(session.id, 'A bug to delete')
  await api.bug(session.id, 'A bug to keep')
  await request.post(`${API_URL}/sessions/${session.id}/end`)

  await test.step('delete a bug: it asks once, then it is gone', async () => {
    await page.goto(`/projects/${project.id}/bugs`)
    const row = page.getByRole('article', { name: 'A bug to delete' })
    await row.getByRole('button', { name: /A bug to delete/ }).click()
    await row.getByRole('button', { name: 'Delete bug' }).click()
    await row.getByRole('group').getByRole('button', { name: 'Cancel' }).click()  // changed my mind
    await row.getByRole('button', { name: 'Delete bug' }).click()
    await row.getByRole('button', { name: 'Yes, delete' }).click()
    await expect(page.getByRole('article', { name: 'A bug to delete' })).toHaveCount(0)
    await expect(page.getByRole('article', { name: 'A bug to keep' })).toBeVisible()
  })

  await test.step('delete a session from the Debriefs page', async () => {
    await page.goto(`/projects/${project.id}/debriefs`)
    const row = page.getByLabel('Explore something to throw away')
    await row.getByRole('button', { name: 'Delete session' }).click()
    await row.getByRole('button', { name: 'Yes, delete' }).click()
    await expect(page.getByText('No debriefs yet')).toBeVisible()
    expect((await request.get(`${API_URL}/sessions/${session.id}`)).status()).toBe(404)
  })

  await test.step('archive: hidden from the Projects page until asked, then unarchived', async () => {
    await page.goto(`/projects/${project.id}/settings`)
    await page.getByRole('button', { name: 'Archive project' }).click()
    await expect(page.getByRole('button', { name: 'Unarchive project' })).toBeVisible()
    await page.goto('/projects')
    await expect(page.getByRole('link', { name })).toHaveCount(0)
    await page.getByLabel(/Show archived/).check()
    await expect(page.getByRole('link', { name })).toContainText('ARCHIVED')
    await page.getByRole('link', { name }).click()
    await page.getByRole('link', { name: 'Settings', exact: true }).click()
    await page.getByRole('button', { name: 'Unarchive project' }).click()
    await expect(page.getByRole('button', { name: 'Archive project' })).toBeVisible()
  })

  await test.step('delete the project: only after typing its name', async () => {
    const del = page.getByRole('button', { name: 'Delete project' })
    await expect(del).toBeDisabled()
    await page.getByLabel(/type its name/).fill(name.toLowerCase())
    await expect(del).toBeDisabled()
    await page.getByLabel(/type its name/).fill(name)
    await del.click()
    await expect(page).toHaveURL(/\/projects$/)
    await expect(page.getByRole('status')).toContainText(`Deleted ${name}`)
    await expect(page.getByRole('link', { name })).toHaveCount(0)
    expect((await request.get(`${API_URL}/projects/${project.id}`)).status()).toBe(404)
  })
})

test('housekeeping: a deleted session says so, and its charter can be explored again', async ({ page, api, request, appErrors }) => {
  const project = await api.newProject('AddisVerify Deleted Session')
  const charter = await api.charter(project.id, 'Explore expiry dates with today, yesterday and tomorrow to discover whether expired documents are rejected')
  const session = await (await request.post(`${API_URL}/sessions`, { data: { charter_id: charter.id, tester_id: 'Attu' } })).json()
  await page.goto(`/projects/${project.id}/sessions/${session.id}`)
  await expect(page.getByText(/Explore expiry dates/).first()).toBeVisible()

  await request.delete(`${API_URL}/sessions/${session.id}`)  // e.g. "Delete session" on its debrief, in another tab
  await page.reload()
  const gone = page.getByRole('alert', { name: 'Session not found' })
  await expect(gone).toContainText("This session doesn't exist any more: it was deleted")
  await page.screenshot({ path: 'test-results/session-deleted.png' })
  await gone.getByRole('link', { name: 'Stories' }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/stories$`))
  // The page asked for the deleted session on purpose: those 404s are the test, not a failure.
  const expected = appErrors.filter((e) => e.includes(`/sessions/${session.id}`) && e.endsWith('-> 404'))
  expect(expected.length).toBeGreaterThan(0)
  appErrors.splice(0, appErrors.length, ...appErrors.filter((e) => !expected.includes(e)))
})
