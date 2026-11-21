import { API_URL, expect, test } from './fixtures'
import { readFileSync } from 'fs'

const EXPOSE = { 'Access-Control-Expose-Headers': 'X-Frame-Seq, X-Frame-Url' }  // as the backend sends them

// Watching Mirmera's browser: each step's picture with a box where the agent acted (replay),
// and the live picture while it works.
test("Mirmera's browser: replay each step, and watch it live", async ({ page, api, request }) => {
  const project = await api.newProject('Agent Browser')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { app_url: 'http://localhost:5190' } })
  const charter = await api.charter(project.id, 'Explore document search with odd names to discover what fails')
  let run = await (await request.post(`${API_URL}/charters/${charter.id}/explore`, { data: {} })).json()
  await expect.poll(async () => {
    run = await (await request.get(`${API_URL}/exploration-runs/${run.id}`)).json()
    return run.status
  }, { timeout: 60_000 }).toBe('finished')
  const framed = run.steps.filter((s: { frame?: string }) => s.frame)
  const last = framed[framed.length - 1]

  await test.step('after the run: its recording plays like a video, the caption follows the step', async () => {
    expect(run.video).toMatch(/\.webm$/)
    await page.goto(`/projects/${project.id}/sessions/${run.session_id}`)
    const view = page.getByRole('region', { name: "Mirmera's browser" })
    await expect(view.getByRole('heading', { name: "Mirmera's browser" })).toBeVisible()
    await expect(view.getByText('Recording', { exact: true })).toBeVisible()
    const film = view.getByLabel("Recording of Mirmera's run")
    await expect.poll(() => film.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThan(0)
    await expect(view.getByLabel('Step on screen')).toContainText(`Step ${run.steps[0].n}`)

    await view.getByRole('button', { name: 'Play' }).click()
    await expect(view.getByRole('button', { name: 'Pause' })).toBeVisible()
    await expect.poll(() => film.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0.3)
    await view.getByRole('button', { name: 'Pause' }).click()
    await expect(view.getByRole('button', { name: 'Play' })).toBeVisible()

    // Jump from step to step: the film goes there, the caption and the box follow.
    await film.evaluate((v: HTMLVideoElement) => { v.currentTime = 0 })
    await view.getByRole('button', { name: 'Next step' }).click()
    const second = run.steps[1]
    await expect(view.getByLabel('Step on screen')).toContainText(`Step ${second.n}`)
    await expect.poll(() => film.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(second.t, 1)
    await expect(view.getByLabel('Where it acted')).toBeVisible()
    await view.getByRole('button', { name: 'Speed' }).click()
    await expect(view.getByRole('button', { name: 'Speed' })).toHaveText('2×')
    await expect.poll(() => film.evaluate((v: HTMLVideoElement) => v.playbackRate)).toBe(2)
  })

  await test.step('its network log downloads as a HAR, with every request the browser made', async () => {
    expect(run.har).toMatch(/^network-.*\.har$/)
    const view = page.getByRole('region', { name: "Mirmera's browser" })
    const link = view.getByRole('link', { name: 'Network log (HAR)' })
    await expect(link).toBeVisible()
    const [download] = await Promise.all([page.waitForEvent('download'), link.click()])
    expect(download.suggestedFilename()).toMatch(/^mirmera-.*\.har$/)
    const har = JSON.parse(readFileSync(await download.path(), 'utf-8'))
    expect(har.log.creator.name).toBe('Mirmera')
    expect(har.log.entries.some((e: { request: { url: string } }) => e.request.url.startsWith('http://localhost:5190'))).toBeTruthy()
    if (process.env.SHOTS) await view.screenshot({ path: `${process.env.SHOTS}/network-log.png` })
  })

  await test.step('a run without a recording plays its step pictures one after another', async () => {
    await page.route(`**/sessions/${run.session_id}/exploration`, (route) => route.fulfill({ json: { ...run, video: null } }))
    await page.goto(`/projects/${project.id}/sessions/${run.session_id}`)
    const view = page.getByRole('region', { name: "Mirmera's browser" })
    await expect(view.getByText('Steps', { exact: true })).toBeVisible()
    await expect(view.getByRole('img', { name: `Step ${last.n}: ${last.action}` })).toBeVisible()
    await expect.poll(() => view.getByRole('img', { name: `Step ${last.n}: ${last.action}` })
      .evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(1280)
    await expect(view.getByLabel('Where it acted')).toBeVisible()  // the button it clicked
    await view.getByRole('button', { name: 'Play' }).click()  // from the end: starts again at the first
    await expect(view.getByRole('img', { name: `Step ${framed[0].n}: ${framed[0].action}` })).toBeVisible()
    await expect(view.getByRole('img', { name: `Step ${framed[1].n}: ${framed[1].action}` })).toBeVisible({ timeout: 4000 })
    await view.getByRole('button', { name: 'Pause' }).click()
    await page.unroute(`**/sessions/${run.session_id}/exploration`)
  })

  await test.step('a step Mirmera took itself, because the AI was stuck, is marked', async () => {
    const steps = run.steps.map((s: { n: number }, i: number) => (i === 0 ? { ...s, by: 'Mirmera' } : s))
    await page.route(`**/sessions/${run.session_id}/exploration`, (route) => route.fulfill({ json: { ...run, steps, video: null } }))
    await page.goto(`/projects/${project.id}/sessions/${run.session_id}`)
    const view = page.getByRole('region', { name: "Mirmera's browser" })
    await view.getByRole('button', { name: 'Play' }).click()  // from the end: starts again at the first
    await view.getByRole('button', { name: 'Pause' }).click()
    await expect(view.getByLabel('Step on screen')).toContainText(`Step ${run.steps[0].n}`)
    await expect(view.getByLabel('Step on screen').getByTitle('The AI was stuck, so Mirmera took this step itself')).toHaveText('Mirmera stepped in')
    if (process.env.SHOTS) await view.screenshot({ path: `${process.env.SHOTS}/stepped-in.png` })
    await page.unroute(`**/sessions/${run.session_id}/exploration`)
  })

  await test.step('while it works: the live picture, marked LIVE (its run is faked as still going)', async () => {
    const jpeg = await (await request.get(`${API_URL}/exploration-runs/${run.id}/steps/${last.n}/frame`)).body()
    await page.route(`**/sessions/${run.session_id}/exploration`, async (route) =>
      route.fulfill({ json: { ...run, status: 'running', finished_at: null } }))
    await page.route(`**/exploration-runs/${run.id}/frame?after=*`, (route) => route.request().url().endsWith('after=-1')
      ? route.fulfill({ body: jpeg, contentType: 'image/jpeg', headers: { 'X-Frame-Seq': '7', 'X-Frame-Url': 'http://localhost:5190/?q=test', ...EXPOSE } })
      : route.fulfill({ status: 204, headers: { 'X-Frame-Seq': '7', ...EXPOSE } }))
    await page.goto(`/projects/${project.id}/sessions/${run.session_id}`)
    const view = page.getByRole('region', { name: "Mirmera's browser" })
    await expect(view.getByRole('status', { name: 'Live' })).toHaveText('LIVE')
    await expect(view.getByRole('img', { name: "Mirmera's browser, live" })).toBeVisible()
    await expect(view).toContainText('http://localhost:5190/?q=test')
    await view.getByRole('button', { name: 'Previous step' }).click()  // look back while it works
    await expect(view.getByText('Earlier step')).toBeVisible()
    await expect(view.getByRole('img', { name: /^Step \d+: / })).toBeVisible()
    await view.getByRole('button', { name: 'Back to live' }).click()
    await expect(view.getByRole('status', { name: 'Live' })).toBeVisible()

    // Pair with it: a hint for its next step.
    await page.route(`**/exploration-runs/${run.id}/hints`, async (route) => {
      expect(route.request().postDataJSON()).toEqual({ text: 'Try a Passport next' })
      await route.fulfill({ json: { ...run, status: 'running', finished_at: null, hints: [
        { id: 'h1', text: 'Try a Passport next', by: 'Attu', at: new Date().toISOString(), step: null }] } })
    })
    const pair = view.getByRole('group', { name: 'Pair with Mirmera' })
    await pair.getByLabel('Tell Mirmera what to try next').fill('Try a Passport next')
    await pair.getByRole('button', { name: 'Send' }).click()
    await expect(pair).toContainText('Attu: Try a Passport next · waiting for its next step')
    await expect(pair.getByLabel('Tell Mirmera what to try next')).toHaveValue('')
    await page.unrouteAll({ behavior: 'ignoreErrors' })
  })

  await test.step('after it ends: the debrief opens the session to look at, notes and recording still there', async () => {
    expect((await request.post(`${API_URL}/sessions/${run.session_id}/end`)).ok()).toBeTruthy()
    await page.goto(`/projects/${project.id}/sessions/${run.session_id}`)
    await expect(page).toHaveURL(/\/debrief$/)  // an ended session still opens its debrief
    await page.getByRole('link', { name: 'Open the session →' }).click()
    await expect(page).toHaveURL(/\?view=1$/)
    await expect(page.getByRole('status', { name: 'View only' })).toContainText('View only')
    const notes = await api.notes(run.session_id)
    expect(notes.length).toBeGreaterThan(0)  // the agent logs what it tried
    await expect(page.getByText(notes[0].content, { exact: true }).first()).toBeVisible()
    await expect(page.getByRole('button', { name: 'End Session' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Pause' })).toHaveCount(0)
    await expect(page.getByPlaceholder('What are you observing?')).toHaveCount(0)
    await expect(page.getByRole('tablist', { name: 'Tools' })).toHaveCount(0)
    const view = page.getByRole('region', { name: "Mirmera's browser" })
    await expect(view.getByRole('link', { name: 'Network log (HAR)' })).toBeVisible()
    await expect(view.getByLabel("Recording of Mirmera's run")).toBeVisible()
    if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/session-view-only.png` })
    await page.getByRole('link', { name: 'Open the debrief' }).click()
    await expect(page).toHaveURL(/\/debrief$/)
  })
})
