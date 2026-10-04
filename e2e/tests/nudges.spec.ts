import { API_URL, expect, test } from './fixtures'

const TARGET = 'http://localhost:5190/'

test('test-idea nudges: charter ideas, a problem the browser saw, use one, try one, hide one', async ({ page, api, request }) => {
  test.setTimeout(60_000)
  // Its own project: nudges look at the project's other sessions.
  const project = await api.newProject('AddisVerify Nudges')
  const created = await request.post(`${API_URL}/projects/${project.id}/charters`, { data: {
    mission_statement: 'Explore document upload', test_ideas: ['Upload a HEIC photo', 'Upload while offline'] } })
  const charter = await created.json()
  const session = await api.session(charter.id)
  const ideas = page.getByRole('region', { name: 'Test ideas' })

  await test.step('a fresh session shows the charter ideas', async () => {
    await page.goto(`/projects/${project.id}/sessions/${session.id}`)
    await expect(ideas).toContainText('Test ideas · 2')
    await expect(ideas.getByRole('article')).toHaveCount(2)
    await expect(ideas.getByRole('article').first()).toContainText('Charter test ideas')
  })

  await test.step('a suggestion goes into the note box; logging it covers the idea', async () => {
    await ideas.getByRole('button', { name: 'Upload a HEIC photo' }).click()
    const box = page.getByRole('textbox', { name: 'What are you observing?' })
    await expect(box).toHaveValue('Tried Upload a HEIC photo: ')
    await expect(box).toBeFocused()
    await box.pressSequentially('rejected with a clear message')
    await page.getByRole('button', { name: 'Log Note' }).click()
    await expect(ideas.getByRole('article')).toHaveCount(1)
  })

  await test.step('the recorded browser sees a failing request: investigate it first', async () => {
    const panel = page.getByRole('complementary', { name: 'Browser' })
    await panel.getByLabel('App URL').fill(TARGET)
    await panel.getByRole('button', { name: 'Open browser' }).click()
    await expect(panel.getByRole('listitem', { name: `GET ${TARGET}api/documents → 500` })).toBeVisible()
    const problem = ideas.getByRole('article', { name: 'Investigate: GET /api/documents → 500' })
    await expect(problem).toBeVisible({ timeout: 15_000 })
    await expect(ideas.getByRole('article').first()).toHaveAccessibleName(/^Investigate: /)  // problems come first
    await expect(ideas.getByRole('article', { name: 'Investigate: Upload widget failed to init' })).toBeVisible()  // the console error too
    await expect(problem).toContainText('no bug or note covers it yet')
    await problem.getByRole('button', { name: 'Write a note' }).click()
    await expect(page.getByRole('textbox', { name: 'What are you observing?' })).toHaveValue('Investigated GET /api/documents → 500: ')
    await page.getByRole('textbox', { name: 'What are you observing?' }).fill('')
  })

  await test.step('"Tried it" logs a test note; "Not now" hides the idea', async () => {
    const problem = ideas.getByRole('article', { name: 'Investigate: GET /api/documents → 500' })
    await problem.getByRole('button', { name: 'Tried it' }).click()
    await expect(page.getByText('Tried: Investigate: GET /api/documents → 500')).toBeVisible()
    await ideas.getByRole('article', { name: 'From the charter' }).getByRole('button', { name: 'Not now' }).click()
    await expect(ideas.getByRole('article', { name: 'From the charter' })).toHaveCount(0)
    while (await ideas.getByRole('article').count() > 0) {  // hide the rest
      await ideas.getByRole('article').first().getByRole('button', { name: 'Not now' }).click()
      await page.waitForTimeout(200)
    }
    await expect(ideas).toHaveCount(0)
    await page.reload()
    await expect(page.getByRole('textbox', { name: 'What are you observing?' })).toBeVisible()
    await expect(ideas).toHaveCount(0)  // hidden for the rest of the session
    await page.getByRole('complementary', { name: 'Browser' }).getByRole('button', { name: 'Stop browser' }).click()
  })
})
