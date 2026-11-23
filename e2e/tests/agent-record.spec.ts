import { API_URL, expect, test } from './fixtures'

// The agent's track record on the Bugs page: what testers made of its drafts, by who decided they were problems.
test("bugs: the agent's track record says how far to trust its drafts", async ({ page, api }) => {
  const project = await api.newProject('AddisVerify Track Record')
  const tally = (drafts: number, bugs: number, questions: number, dismissed: number, waiting: number) => ({
    drafts, bugs, questions, dismissed, waiting, kept_share: null, dismissed_for: {}, replayed: 0, every_time: 0, never_again: 0 })
  await page.route(`${API_URL}/projects/${project.id}/agent-record`, (route) => route.fulfill({ json: {
    overall: { ...tally(12, 7, 1, 3, 1), dismissed_for: { 'works as intended': 2, 'not a problem': 1 }, replayed: 11, every_time: 9, never_again: 1 },
    by_judge: [{ judge: 'ai', label: "the AI's judgement", ...tally(5, 2, 1, 2, 0) },
               { judge: 'mirmera', label: "Mirmera's own checks", ...tally(7, 5, 0, 1, 1) }],
    runs: [],
  } }))
  await page.goto(`/projects/${project.id}/bugs`)
  const record = page.getByRole('region', { name: "The agent's track record" })
  await expect(record).toContainText('12 drafts, 7 of 11 reviewed kept as bugs, 1 waiting for review')
  await record.getByRole('button', { expanded: false }).click()
  await expect(record.getByRole('row', { name: "The AI's judgement" })).toContainText('52120')
  await expect(record.getByRole('row', { name: "Mirmera's own checks" })).toContainText('75011')
  await expect(record).toContainText('Dismissed as: works as intended (2), not a problem (1)')
  await expect(record).toContainText('Replayed in fresh browsers: 9 of 11 showed every time, 1 never showed again.')
  if (process.env.SHOTS) await record.screenshot({ path: `${process.env.SHOTS}/agent-record.png` })
})

test('bugs: no track record before the agent has drafted anything', async ({ page, api }) => {
  const project = await api.newProject('AddisVerify No Record')
  await page.goto(`/projects/${project.id}/bugs`)
  await expect(page.getByRole('heading', { name: 'Bugs', level: 1 })).toBeVisible()
  await expect(page.getByRole('region', { name: "The agent's track record" })).toHaveCount(0)
})
