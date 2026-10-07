import { API_URL, expect, test } from './fixtures'

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

  await test.step('after the run: step through what it did, with a box where it acted', async () => {
    await page.goto(`/projects/${project.id}/sessions/${run.session_id}`)
    const view = page.getByRole('region', { name: "Mirmera's browser" })
    await expect(view.getByText('Replay')).toBeVisible()
    const picture = view.getByRole('img', { name: `Step ${last.n}: ${last.action}` })
    await expect(picture).toBeVisible()
    await expect.poll(() => picture.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(1280)
    await expect(view.getByLabel('Where it acted')).toBeVisible()  // the button it clicked
    await expect(view).toContainText(`Step ${last.n}`)
    await view.getByRole('button', { name: 'Previous step' }).click()
    const before = framed[framed.length - 2]
    await expect(view.getByRole('img', { name: `Step ${before.n}: ${before.action}` })).toBeVisible()
    await expect(view.getByRole('slider', { name: 'Step' })).toHaveValue(String(framed.length - 2))
  })

  await test.step('while it works: the live picture, marked LIVE (its run is faked as still going)', async () => {
    const jpeg = await (await request.get(`${API_URL}/exploration-runs/${run.id}/steps/${last.n}/frame`)).body()
    await page.route(`**/charters/${charter.id}/exploration`, async (route) =>
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
    await view.getByRole('button', { name: 'Back to live' }).click()
    await expect(view.getByRole('status', { name: 'Live' })).toBeVisible()
  })
})
