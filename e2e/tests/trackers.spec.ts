import { expect, test } from './fixtures'

// The e2e backend runs with MOCK_TRACKERS=true: GitHub and Jira are in memory,
// each starting with two issues ("Upload widget fails to load on Safari" open,
// "Expired national ID accepted at upload" closed).
test('file a reviewed bug: set up, check duplicates, add an occurrence on GitHub, file new in Jira', async ({ page, api }) => {
  const project = await api.project()
  const charter = await api.charter(project.id, 'Explore ID upload across browsers')
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
    await page.goto(`/projects/${project.id}/bugs`)
    await row.getByRole('button', { name: new RegExp(title) }).click()
    await expect(panel.getByText('Mock trackers (MOCK_TRACKERS=true)', { exact: false })).toBeVisible()
    await panel.getByLabel('GitHub repo for this project').fill('addisverify/kyc')
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
    await panel.getByLabel('Jira project key for this project').fill('KYC')
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

// The same problem reported again: Mirmera knows KYC-3 is it (its fingerprint), without AI.
test('file a reviewed bug: the same problem already filed is found first and picked', async ({ page, api, request }) => {
  const project = await api.project()
  const { API_URL } = await import('./fixtures')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { jira_project_key: 'KYC' } })
  const charter = await api.charter(project.id, 'Explore ID upload across browsers')
  const session = await api.session(charter.id)
  const title = 'Selfie step freezes on retake'
  const review = { kind: 'bug', steps_to_reproduce: ['Open /selfie', 'Retake'], expected: 'A new photo', actual: 'Frozen',
    oracle_heuristic: 'user_expectations', oracle_explanation: 'Retake works elsewhere' }
  const first = await api.bug(session.id, title)
  await api.reviewBug(first.id, review)
  await request.post(`${API_URL}/bugs/${first.id}/file`, { data: { tracker: 'jira' } })
  const again = await api.bug(session.id, `${title}  `)  // the same report, from another tester
  await api.reviewBug(again.id, review)

  await page.goto(`/projects/${project.id}/bugs`)
  const rows = page.getByRole('article', { name: title })
  const row = rows.first()  // newest first: the one not filed yet
  await row.getByRole('button', { name: new RegExp(title) }).click()
  const panel = row.getByRole('region', { name: 'File in tracker' })
  await panel.getByRole('tab', { name: /Jira/ }).click()
  await panel.getByRole('button', { name: 'Check for duplicates' }).click()
  await expect(panel).toContainText(/and the fingerprint mfp[0-9a-f]{12}, open and closed/)
  const same = panel.getByRole('listitem', { name: new RegExp(`KYC-\\d+ ${title}`) })
  await expect(same).toContainText('Already filed')
  await expect(same).toContainText('Mirmera already filed this problem')
  await expect(panel.getByRole('button', { name: /^Add to KYC-\d+$/ })).toBeVisible()  // picked for the tester
  await page.screenshot({ path: 'test-results/same-problem.png' })
  await panel.getByRole('button', { name: 'File as new instead' }).click()
  await expect(panel.getByRole('button', { name: 'File new Jira issue anyway' })).toBeVisible()
})
