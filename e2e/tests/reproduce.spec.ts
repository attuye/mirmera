import { expect, test } from './fixtures'

const TARGET = 'http://localhost:5190/'

// The target app fails GET /api/documents on every load. The mock agent follows
// the recorded actions (none here), then claims the symptom the recording
// points to; Mirmera checks it and reruns the steps to measure reliability.
test('reproduction agent: reproduce a logged bug, follow its steps, make a test from them', async ({ page, api }) => {
  const product = await api.product()
  const charter = await api.charter(product.id, 'Explore the documents page on a slow backend')
  const session = await api.session(charter.id)
  const title = 'Documents API answers 500 on load'

  await test.step('log the bug in the recorded browser', async () => {
    await page.goto(`/sessions/${session.id}`)
    const panel = page.getByRole('complementary', { name: 'Browser' })
    await panel.getByLabel('App URL').fill(TARGET)
    await panel.getByRole('button', { name: 'Open browser' }).click()
    await expect(panel.getByRole('listitem', { name: `GET ${TARGET}api/documents → 500` })).toBeVisible()
    await page.getByRole('button', { name: 'Bug', exact: true }).click()
    await page.getByPlaceholder('What are you observing?').fill(title)
    await page.getByRole('button', { name: 'Log Bug' }).click()
    await expect(page.getByRole('img', { name: `When logged: ${title}` })).toBeVisible()
  })

  const row = page.getByRole('article', { name: title })
  const agent = row.getByRole('region', { name: 'Reproduction agent' })

  await test.step('start the agent on the unreviewed bug and see it reproduce, checked and measured', async () => {
    await page.goto('/bugs')
    await row.getByRole('button', { name: new RegExp(title) }).click()
    await agent.getByLabel('Reruns').fill('2')
    await agent.getByRole('button', { name: 'Reproduce with the agent' }).click()
    const result = agent.getByRole('status', { name: 'Agent result' })
    await expect(result).toContainText('Reproduced · 2 of 2 reruns', { timeout: 60_000 })
    await expect(agent).toContainText(`checked by Mirmera: GET ${TARGET}api/documents -> 500`)
    await expect(agent.getByRole('list', { name: 'Agent steps' }).getByRole('listitem')).toContainText(['verified'])
    await expect(agent.getByRole('img', { name: 'Where the agent stopped' })).toBeVisible()
  })

  await test.step('the found steps become a regression test', async () => {
    await agent.getByRole('button', { name: 'Make a regression test from these steps' }).click()
    await expect(agent.getByRole('button', { name: 'Regression test created' })).toBeDisabled()
    await expect(agent).toContainText('Review the bug as a bug to see it in the Regression test panel.')
  })

  await test.step('after a reload the last run is still there', async () => {
    await page.reload()
    await row.getByRole('button', { name: new RegExp(title) }).click()
    await expect(agent.getByRole('status', { name: 'Agent result' })).toContainText('Reproduced · 2 of 2 reruns')
    await expect(agent.getByRole('button', { name: 'Try again' })).toBeVisible()
  })
})
