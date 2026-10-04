import { API_URL, expect, test } from './fixtures'

test('members: invite a teammate who signs up from the link, change their role, reset a forgotten password', async ({ page, api, browser, request }) => {
  const project = await api.newProject('AddisVerify Members')
  const workspace = await api.workspaceId()
  let link = ''

  await test.step('invite by email from the members page', async () => {
    await page.goto('/account')
    await page.getByRole('list', { name: 'Your workspaces' }).getByRole('listitem').filter({ hasText: 'AddisVerify' })
      .getByRole('link', { name: 'Members' }).click()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('AddisVerify: members')
    const invite = page.getByRole('region', { name: 'Invite' })
    await invite.getByLabel('Invite by email').fill('abebe@team.test')
    await invite.getByLabel('Role').selectOption('tester')
    await invite.getByRole('button', { name: 'Invite' }).click()
    await expect(invite.getByRole('status')).toContainText('Invitation emailed to abebe@team.test')
    link = await invite.getByLabel('Invitation link').inputValue()
    await expect(page.getByRole('region', { name: 'Pending invitations' })).toContainText('abebe@team.test as tester, by Attu')
  })

  await test.step('the teammate opens the link, creates an account and lands in the workspace', async () => {
    const other = await browser.newContext({ storageState: { cookies: [], origins: [] } })
    const p = await other.newPage()
    await p.goto(new URL(link).pathname)
    await expect(p.getByRole('heading', { name: 'Join AddisVerify' })).toBeVisible()
    await expect(p.getByText('Attu invited abebe@team.test to the workspace AddisVerify as a tester.')).toBeVisible()
    await p.getByLabel('Your name').fill('Abebe')
    await p.getByLabel('Choose a password (at least 10 characters)').fill('abebe long passphrase')
    await p.getByRole('button', { name: 'Create account and join' }).click()
    await expect(p.getByRole('link', { name: 'AddisVerify Members' })).toBeVisible()
    await other.close()
  })

  await test.step('the owner sees them and makes them a viewer', async () => {
    await page.reload()
    const abebe = page.getByRole('region', { name: 'Members' }).getByRole('listitem', { name: 'Abebe' })
    await expect(abebe).toContainText('abebe@team.test')
    await abebe.getByLabel('Role of Abebe').selectOption('viewer')
    await expect(abebe.getByLabel('Role of Abebe')).toHaveValue('viewer')
    const members = await (await request.get(`${API_URL}/workspaces/${workspace}/members`)).json()
    expect(members.members.find((m: { name: string }) => m.name === 'Abebe').role).toBe('viewer')
    expect(members.invitations).toEqual([])
  })

  await test.step('the teammate forgets their password and resets it from the emailed link', async () => {
    const other = await browser.newContext({ storageState: { cookies: [], origins: [] } })
    const p = await other.newPage()
    await p.goto('/signin')
    await p.getByRole('link', { name: 'Forgot your password?' }).click()
    await p.getByLabel('Email').fill('abebe@team.test')
    await p.getByRole('button', { name: 'Send a reset link' }).click()
    await expect(p.getByRole('status')).toContainText('If abebe@team.test has an account')
    const outbox: { to: string; text: string }[] = await (await request.get(`${API_URL}/mock-email`)).json()
    const resetLink = outbox.filter((m) => m.to === 'abebe@team.test').pop()!.text.match(/https?:\/\/\S+\/reset\/\S+/)![0]
    await p.goto(new URL(resetLink).pathname)
    await p.getByLabel('New password (at least 10 characters)').fill('a fresh passphrase')
    await p.getByRole('button', { name: 'Save and sign in' }).click()
    await expect(p.getByRole('link', { name: 'AddisVerify Members' })).toBeVisible()
    await p.goto(`/projects/${project.id}/charters/suggest`)  // a viewer can look
    await expect(p.getByRole('heading', { name: 'Charters', level: 1 })).toBeVisible()
    await other.close()
  })
})
