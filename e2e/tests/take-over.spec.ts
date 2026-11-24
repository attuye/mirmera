import { API_URL, expect, test } from './fixtures'

// Explore with me: take the agent's browser, see your steps in its trail, hand it back.
// (The run is faked here: a real one opens a browser window on the tester's screen.)
test('explore with me: take over the browser, then hand it back', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Take Over')
  const charter = await api.charter(project.id, 'Explore the expiry date to discover whether expired documents are rejected')
  const session = await api.session(charter.id)
  let takeover: { state: string; by: string } | null = null
  const steps = [
    { n: 1, thought: 'Open the form', action: 'open http://localhost:4100/verify', kind: 'goto', status: 'ok', detail: '', url: 'http://localhost:4100/verify' },
  ]
  const run = () => ({
    id: 'run-me', project_id: project.id, charter_id: charter.id, session_id: session.id, status: 'running',
    start_url: 'http://localhost:4100/', max_steps: 25, steps, bug_ids: [], summary: null, detail: null,
    created_at: new Date().toISOString().replace('Z', ''), finished_at: null, with_me: true, takeover,
  })
  await page.route('**/exploration-runs/run-me/frame?*', (route) => route.fulfill({ status: 204 }))
  await page.route(`**/sessions/${session.id}/exploration`, (route) => route.fulfill({ json: run() }))
  await page.route('**/exploration-runs/run-me', (route) => route.fulfill({ json: run() }))
  await page.route('**/exploration-runs/run-me/take-over', (route) => {
    takeover = { state: 'yours', by: 'Attu' }
    steps.push({ n: 2, thought: '', action: 'Attu took over the browser', kind: 'takeover', status: 'ok', by: 'Attu',
      detail: 'the agent stopped after its step; the browser is theirs until they hand it back', url: 'http://localhost:4100/verify' } as never,
    { n: 3, thought: '', action: 'select "Passport" in label "Document type"', kind: 'you', status: 'ok', by: 'Attu', detail: '', url: 'http://localhost:4100/verify' } as never)
    return route.fulfill({ json: run() })
  })
  await page.route('**/exploration-runs/run-me/hand-back', (route) => {
    takeover = { state: 'back', by: 'Attu' }
    return route.fulfill({ json: run() })
  })

  await page.goto(`/projects/${project.id}/sessions/${session.id}`)
  const browser = page.getByRole('region', { name: "Mirmera's browser" })
  const control = browser.getByRole('group', { name: 'Take over the browser' })
  await expect(control.getByRole('status')).toHaveText('Its browser is a window on your screen.')
  await control.getByRole('button', { name: 'Take over' }).click()
  await expect(control.getByRole('status')).toContainText("It's your browser: use the Mirmera window on your screen.")
  await expect(control.getByRole('button', { name: 'Hand back' })).toBeVisible()
  if (process.env.SHOTS) await browser.screenshot({ path: `${process.env.SHOTS}/take-over.png` })
  await control.getByRole('button', { name: 'Hand back' }).click()
  await expect(control.getByRole('button', { name: 'Take over' })).toBeVisible()
})

test('explore with me: the option asks for a browser window', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify With Me')
  const story = await (await request.post(`${API_URL}/projects/${project.id}/stories`, { data: { title: 'Verify my identity', key: 'ME-1' } })).json()
  const charter = await api.charter(project.id, 'Explore names in Ge\'ez script to discover whether they are accepted')
  await request.put(`${API_URL}/stories/${story.id}/charters`, { data: { ids: [charter.id] } })
  let sent: Record<string, unknown> | null = null
  await page.route(`**/charters/${charter.id}/explore`, async (route) => {
    sent = route.request().postDataJSON()  // not started for real: a finished run comes back
    await route.fulfill({ json: { id: 'run-x', project_id: project.id, charter_id: charter.id, session_id: null, status: 'finished',
      start_url: 'http://localhost:4100/', max_steps: 25, steps: [], bug_ids: [], summary: null, detail: null,
      created_at: new Date().toISOString().replace('Z', ''), finished_at: null, with_me: true, takeover: null } })
  })
  await page.goto(`/projects/${project.id}/stories/${story.id}`)
  const explore = page.getByRole('group', { name: 'Mirmera explores' })
  await explore.getByLabel('with me').check()
  await explore.getByRole('button', { name: 'Let Mirmera explore' }).click()
  await expect.poll(() => sent).toEqual({ with_me: true })
})
