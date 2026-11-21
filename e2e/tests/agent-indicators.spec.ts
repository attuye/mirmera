import { API_URL, expect, test } from './fixtures'

// While Mirmera works, the page shows it: on the story's row in Stories, and beside the clock of
// the session its agent is running. (Mirmera's work is faked here, so the test can see it mid-run.)
test('Mirmera at work: on the stories list and beside the session clock', async ({ page, api, request }) => {
  const project = await api.newProject('TumaTuma Indicators')
  const story = await (await request.post(`${API_URL}/projects/${project.id}/stories`, {
    data: { key: 'SCRUM-11', title: 'Sender verifies identity with a national ID' } })).json()
  const other = await (await request.post(`${API_URL}/projects/${project.id}/stories`, {
    data: { key: 'SCRUM-12', title: 'Sender adds a recipient in Kenya' } })).json()
  const mission = 'Explore expiry dates with valid names and numbers'
  const charter = await (await request.post(`${API_URL}/projects/${project.id}/charters`, {
    data: { mission_statement: mission, story_ids: [story.id] } })).json()
  const session = await (await request.post(`${API_URL}/sessions`, { data: { charter_id: charter.id, tester_id: 'Mirmera agent' } })).json()

  const now = new Date().toISOString().replace('Z', '')
  const steps = Array.from({ length: 12 }, (_, i) => ({
    n: i + 1, thought: 'Keep the other fields valid', action: i === 11 ? 'fill label "Expiry date" = "2020-01-01"' : 'click button "Verify"',
    kind: i === 11 ? 'fill' : 'click', status: 'ok', detail: '', url: 'http://localhost:4000/verify' }))
  const running = {
    id: 'run-1', kind: 'explore', state: 'running', stage: 'explore', title: mission, issue_key: 'SCRUM-11',
    project_id: project.id, story_id: story.id, started_by: 'Attu', message: null, started_at: now, finished_at: null,
    stages: [{ key: 'explore', label: 'Explore in the app', status: 'active' }, { key: 'done', label: 'Done', status: 'todo' }],
    exploration: { run_id: 'run-1', status: 'running', charter_id: charter.id, mission, session_id: session.id, steps_used: 12,
      max_steps: 25, actions: 12, recent: steps.slice(-6), bugs: [], summary: null, detail: null },
  }
  await page.route('**/exploration-runs/run-1/frame?*', (route) => route.fulfill({ status: 204 }))  // no live picture here
  await page.route('**/workspaces/*/agent-activity', (route) => route.fulfill({ json: { items: [running], running: 1 } }))

  await test.step('the story Mirmera is on says so in the list; the others do not', async () => {
    await page.goto(`/projects/${project.id}/stories`)
    const list = page.getByRole('region', { name: 'Your stories' })
    await expect(list.getByRole('listitem', { name: story.title }).getByRole('status', { name: 'Mirmera is working on this story' }))
      .toHaveText('Mirmera: Exploring in the app · step 12 of 25')
    await expect(list.getByRole('listitem', { name: other.title }).getByRole('status')).toHaveCount(0)
  })

  let cancelled = false
  await page.route(`**/sessions/${session.id}/exploration`, (route) => route.fulfill({ json: {
    id: 'run-1', project_id: project.id, charter_id: charter.id, session_id: session.id, status: cancelled ? 'cancelled' : 'running',
    start_url: 'http://localhost:4000', max_steps: 25, steps, bug_ids: [], summary: null, detail: null, started_by: 'Attu',
    ai_provider: 'mock', ai_model: 'mock', created_at: now, finished_at: null } }))
  await page.route('**/exploration-runs/run-1/cancel', (route) => { cancelled = true; return route.fulfill({ json: {} }) })

  await test.step('beside the session clock: how far the agent has got, and a way to stop it', async () => {
    await page.goto(`/projects/${project.id}/sessions/${session.id}`)
    const atWork = page.getByRole('status', { name: 'Mirmera at work' })
    await expect(atWork).toContainText('Mirmera is exploring · step 12 of 25')
    await expect(atWork).toContainText('fill label "Expiry date" = "2020-01-01"')
    await expect(page.getByRole('button', { name: 'End Session' })).toBeDisabled()  // stop Mirmera first
    await atWork.getByRole('button', { name: 'Stop' }).click()
    await expect(atWork).toContainText('Mirmera is stopping')
    await expect(atWork).toBeHidden()  // the run has stopped
    await expect(page.getByRole('button', { name: 'End Session' })).toBeEnabled()
  })
})
