import { expect, test } from './fixtures'

const TARGET = 'http://localhost:5190/'

test('recorded browser: signals, screenshots, bug evidence and the trace', async ({ page, api, request }) => {
  // The shared project.
  const project = await api.project()
  const charter = await api.charter(project.id, 'Explore ID upload when the storage service fails')
  const session = await api.session(charter.id)
  const panel = page.getByRole('complementary', { name: 'Browser' })

  await test.step('open the recorded browser on the app under test', async () => {
    await page.goto(`/projects/${project.id}/sessions/${session.id}`)
    await panel.getByLabel('App URL').fill(TARGET)
    await panel.getByRole('button', { name: 'Open browser' }).click()
    await expect(panel.getByRole('heading', { name: 'Recording browser' })).toBeVisible()
    await expect(panel.getByText('KYC upload', { exact: true })).toBeVisible()
  })

  await test.step('what the page reports shows up while testing, repeats counted once', async () => {
    const reported = panel.getByRole('region', { name: 'What the page reported' })
    const widget = reported.getByRole('listitem', { name: 'Upload widget failed to init' })
    await expect(widget).toBeVisible()
    await expect(widget).toContainText('×2')
    await expect(reported.getByRole('listitem', { name: `GET ${TARGET}api/documents → 500` })).toBeVisible()
    await expect(panel.getByRole('region', { name: 'Pages visited' })).toContainText('localhost:5190/')
  })

  await test.step('a screenshot lands in the timeline', async () => {
    await panel.getByRole('button', { name: 'Screenshot' }).click()
    const shot = page.getByRole('img', { name: 'Screenshot of KYC upload' })
    await expect(shot).toBeVisible()
    await expect.poll(() => shot.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0)
  })

  await test.step('logging a bug captures the screen at that moment', async () => {
    await page.getByRole('button', { name: 'Bug', exact: true }).click()
    await page.getByPlaceholder('What are you observing?').fill('Upload widget never loads')
    await page.getByRole('button', { name: 'Log Bug' }).click()
    await expect(page.getByRole('img', { name: 'When logged: Upload widget never loads' })).toBeVisible()
  })

  await test.step('the bug shows its screenshot and what the page reported before it', async () => {
    await page.goto(`/projects/${project.id}/bugs`)
    const bug = page.getByRole('article', { name: 'Upload widget never loads' })
    await bug.getByRole('button', { name: /Upload widget never loads/ }).click()
    const recorded = bug.getByRole('region', { name: 'Recorded evidence' })
    await expect(recorded.getByRole('img', { name: 'When logged: Upload widget never loads' })).toBeVisible()
    await expect(recorded.getByRole('listitem', { name: 'Upload widget failed to init' })).toBeVisible()
  })

  await test.step('the AI draft uses the recorded evidence and shows which lines it relied on', async () => {
    const bug = page.getByRole('article', { name: 'Upload widget never loads' })
    await bug.getByRole('button', { name: 'Draft report with AI' }).click()
    await expect(bug.getByLabel(/Steps to reproduce/)).toHaveValue(new RegExp(`^Open ${TARGET}`))
    const used = bug.getByRole('note', { name: 'Browser evidence the draft used' })
    await expect(used).toContainText(/\+0 min \[(console error ×2|failed request)\]/)
    await expect(bug.getByText(/it only knows your notes and what the browser recorded/)).toBeVisible()
  })

  await test.step('ending the session closes the browser and keeps the trace and network log', async () => {
    await page.goto(`/projects/${project.id}/sessions/${session.id}`)
    await page.getByRole('button', { name: 'End Session' }).click()
    await expect(page).toHaveURL(new RegExp(`/sessions/${session.id}/debrief$`))
    const recorded = page.getByRole('region', { name: 'Recorded evidence' })
    await expect(recorded).toContainText('2 console errors · 1 failed request · 1 page visited · 2 screenshots')
    await expect(page.getByRole('textbox', { name: 'Results', exact: true }))
      .toHaveValue(/\(mock\) Browser recorded: 2 console errors, 1 failed request; 1 page visited; 2 screenshots\./)
    for (const name of ['Download trace', 'Download network log (HAR)']) {
      const href = await recorded.getByRole('link', { name }).getAttribute('href')
      const file = await request.get(href!)
      expect(file.ok()).toBeTruthy()
      expect((await file.body()).length).toBeGreaterThan(100)
    }
  })
})
