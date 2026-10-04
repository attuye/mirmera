import { expect, test } from './fixtures'

test('notifications: set up Telegram and Slack, send a test, hear about a high-severity bug', async ({ page, api }) => {
  const project = await api.newProject('AddisVerify Notifications')
  const charter = await api.charter(project.id, 'Explore passport upload')
  const session = await api.session(charter.id)
  const bug = await api.bug(session.id, 'Passport upload fails for large files')

  await page.goto(`/projects/${project.id}/settings`)
  const section = page.getByRole('region', { name: 'Notifications' })
  await expect(section).toContainText('Mock mode: messages are kept here, not sent.')

  await test.step('nothing set up yet: nothing to test', async () => {
    await expect(section.getByRole('button', { name: 'Send a test message' })).toBeDisabled()
  })

  await test.step('a chat id and Slack, saved, then a test message', async () => {
    await section.getByLabel('Telegram chat id').fill('123456789')
    await section.getByLabel('Send to Slack').check()
    await expect(section.getByRole('button', { name: 'Send a test message' })).toBeDisabled()  // save first
    await section.getByRole('button', { name: 'Save notifications' }).click()
    await expect(section.getByRole('status')).toHaveText('Saved.')
    await section.getByRole('button', { name: 'Send a test message' }).click()
    const recent = section.getByRole('list', { name: 'Recent notifications' })
    await expect(recent.getByRole('listitem').filter({ hasText: 'telegram kept (mock)' })).toContainText('Test message')
    await expect(recent.getByRole('listitem').filter({ hasText: 'slack kept (mock)' })).toContainText('Test message')
  })

  await test.step('a bug reviewed as high severity is announced', async () => {
    await api.reviewBug(bug.id, {
      kind: 'bug', severity: 'high', expected: 'The passport uploads', actual: 'A 6 MB scan fails with "Network error"',
      oracle_heuristic: 'claims', oracle_explanation: 'The help page says files up to 10 MB are accepted',
    })
    await page.reload()
    await expect(section.getByRole('list', { name: 'Recent notifications' }).getByRole('listitem')
      .filter({ hasText: 'High-severity bug: Passport upload fails for large files' })).toHaveCount(2)  // telegram and slack
  })
})
