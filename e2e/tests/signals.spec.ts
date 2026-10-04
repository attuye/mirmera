import { expect, test } from './fixtures'

const TARGET = 'http://localhost:5190/'

test('recorded browser: slow requests and accessibility findings, with a nudge to check by hand', async ({ page, api }) => {
  test.setTimeout(60_000)
  const project = await api.newProject('AddisVerify Signals')
  const charter = await api.charter(project.id, 'Explore sign-up for people using assistive technology')
  const session = await api.session(charter.id)
  const panel = page.getByRole('complementary', { name: 'Browser' })

  await page.goto(`/projects/${project.id}/sessions/${session.id}`)
  await panel.getByLabel('App URL').fill(TARGET + 'signup')
  await panel.getByRole('button', { name: 'Open browser' }).click()

  await test.step('a slow call shows with the other signals', async () => {
    await expect(panel.getByRole('listitem', { name: `GET ${TARGET}api/slow took longer than 2 s` })).toBeVisible({ timeout: 15_000 })
  })

  await test.step('accessibility findings are folded under their own heading', async () => {
    const a11y = panel.getByText(/Accessibility: \d+ findings?/)
    await expect(a11y).toBeVisible()
    await a11y.click()
    await expect(panel.getByRole('listitem', { name: 'Form field without a label: input#phone' })).toBeVisible()
    await expect(panel.getByRole('listitem', { name: 'Button without a name: button' })).toBeVisible()
    await expect(panel.getByRole('listitem', { name: 'Page has no language (<html lang>)' })).toBeVisible()
    await expect(panel.getByRole('listitem', { name: /^Low contrast text .*By signing up you accept the terms/ })).toBeVisible()
  })

  await test.step('test ideas suggest checking by hand, and investigating the slow call', async () => {
    const ideas = page.getByRole('region', { name: 'Test ideas' })
    await expect(ideas.getByRole('article', { name: /^Accessibility: \d+ findings recorded$/ })).toBeVisible({ timeout: 15_000 })
    await expect(ideas.getByRole('article', { name: /^Accessibility/ })).toContainText('Use the main form with the keyboard only')
    await expect(ideas.getByRole('article', { name: `Investigate: GET ${TARGET}api/slow took longer than 2 s` })).toContainText('Performance: is it slow for users?')
    await panel.getByRole('button', { name: 'Stop browser' }).click()
  })
})
