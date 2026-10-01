import { expect, test } from './fixtures'

// The e2e backend runs with MOCK_TRACKERS=true: GitHub and Jira are in memory,
// each starting with two issues ("Upload widget fails to load on Safari" open,
// "Expired national ID accepted at upload" closed).
test('file a reviewed bug: set up, check duplicates, add an occurrence on GitHub, file new in Jira', async ({ page, api }) => {
  const product = await api.product()
  const charter = await api.charter(product.id, 'Explore ID upload across browsers')
  const session = await api.session(charter.id)
  const title = 'Upload widget fails to load on Chrome'
  const bug = await api.bug(session.id, title)
  await api.reviewBug(bug.id, {
    kind: 'bug', steps_to_reproduce: ['Open /upload in Chrome 129', 'Wait 10 seconds'],
    expected: 'The upload widget appears', actual: 'Spinner forever',
    oracle_heuristic: 'comparable_products', oracle_explanation: 'It works in Firefox',
  })

  const row = page.getByRole('article', { name: title })
  const panel = row.getByRole('region', { name: 'File in tracker' })

  await test.step('open the reviewed bug; GitHub needs the repo first', async () => {
    await page.goto('/bugs')
    await row.getByRole('button', { name: new RegExp(title) }).click()
    await expect(panel.getByText('Mock trackers (MOCK_TRACKERS=true)', { exact: false })).toBeVisible()
    await panel.getByLabel('GitHub repo for this product').fill('addisverify/kyc')
    await panel.getByRole('button', { name: 'Save' }).click()
    await expect(panel.getByRole('tab', { name: /GitHub addisverify\/kyc/ })).toBeVisible()
  })

  await test.step('the preview is the reviewed report', async () => {
    await panel.getByRole('button', { name: 'Preview what will be filed' }).click()
    const preview = panel.getByLabel('Issue preview')
    await expect(preview).toContainText('### Steps to reproduce')
    await expect(preview).toContainText('Oracle: Comparable products. It works in Firefox')
  })

  await test.step('the duplicate check finds the Safari issue and rates it', async () => {
    await panel.getByRole('button', { name: 'Check for duplicates' }).click()
    await expect(panel.getByText(/Searched GitHub for upload, widget, fails, load, chrome/)).toBeVisible()
    const safari = panel.getByRole('listitem', { name: '#1 Upload widget fails to load on Safari' })
    await expect(safari).toContainText('Likely duplicate')
  })

  await test.step('add it to #1 as a new occurrence', async () => {
    const safari = panel.getByRole('listitem', { name: '#1 Upload widget fails to load on Safari' })
    await safari.getByRole('button', { name: 'Add to #1 as a new occurrence' }).click()
    await panel.getByLabel('Note for the developers (optional)').fill('Also on Chrome 129')
    await panel.getByRole('button', { name: 'Add to #1' }).click()
    await expect(panel.getByRole('status')).toContainText('Added as a new occurrence to #1')
  })

  await test.step('in Jira the tester files a new issue anyway: the verdict informs, the tester decides', async () => {
    await panel.getByRole('tab', { name: /Jira/ }).click()
    await panel.getByLabel('Jira project key for this product').fill('KYC')
    await panel.getByRole('button', { name: 'Save' }).click()
    await panel.getByRole('button', { name: 'Check for duplicates' }).click()
    await expect(panel.getByRole('listitem', { name: /KYC-1 / })).toContainText('Likely duplicate')
    await panel.getByRole('button', { name: 'File new Jira issue' }).click()
    await expect(panel.getByRole('status')).toContainText('Filed as KYC-3')
  })

  await test.step('after a reload the bug links to both issues', async () => {
    await page.reload()
    await expect(row.getByRole('link', { name: '#1 ↗' })).toBeVisible()
    await expect(row.getByRole('link', { name: 'KYC-3 ↗' })).toBeVisible()
  })
})
