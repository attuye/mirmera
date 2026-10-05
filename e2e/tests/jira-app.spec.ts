import { API_URL, expect, test } from './fixtures'

// The target server stands in for Atlassian: it publishes the signing key and signs Forge
// invocation tokens, as Atlassian does for the real app's calls (see scripts/serve-target.mjs).
const ATLASSIAN = 'http://localhost:5190/fake-forge'
const APP_ID = 'ari:cloud:ecosystem::app/e2e-mirmera'

test('jira app: link a Jira site with a one-time code, see the panel for a story, unlink', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Jira app')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { jira_project_key: 'JAPP' } })
  await request.post(`${API_URL}/projects/${project.id}/stories`, { data: { title: 'Customers can renew an expired ID', key: 'JAPP-7' } })
  const wid = await api.workspaceId()
  const fromJira = async (installation: string) => ({
    Authorization: `Bearer ${await (await request.get(`${ATLASSIAN}/token?installation=${installation}&app=${encodeURIComponent(APP_ID)}`)).text()}`,
  })

  let code = ''
  await test.step('a workspace admin opens Jira from the Account page and makes a link code', async () => {
    await page.goto('/account')
    await expect(page.getByRole('link', { name: 'Jira', exact: true }).first()).toBeVisible()
    await page.goto(`/workspaces/${wid}/jira`)
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(/: Jira$/)
    await expect(page.getByRole('note')).toHaveCount(0)  // JIRA_APP_ID is set on this server
    await expect(page.getByRole('region', { name: 'Linked Jira sites' })).toContainText('None yet.')
    await page.getByRole('button', { name: 'Make a link code' }).click()
    code = (await page.getByLabel('Link code').textContent()) ?? ''
    expect(code).toMatch(/^mjl_/)
  })

  await test.step('in Jira, the admin page sends the code: the site is linked', async () => {
    const before = await request.post(`${API_URL}/jira-app/issue-panel`, { data: { issue_key: 'JAPP-7' }, headers: await fromJira('e2e-site') })
    expect(before.status()).toBe(409)
    const linked = await request.post(`${API_URL}/jira-app/link`, {
      data: { code, site_url: 'https://e2e.atlassian.net' }, headers: await fromJira('e2e-site'),
    })
    expect(linked.ok()).toBeTruthy()
    expect((await linked.json()).linked).toBe(true)
    const unsigned = await request.post(`${API_URL}/jira-app/link`, { data: { code }, headers: { Authorization: 'Bearer forged' } })
    expect(unsigned.status()).toBe(401)

    await page.reload()
    const sites = page.getByRole('region', { name: 'Linked Jira sites' })
    await expect(sites).toContainText('https://e2e.atlassian.net')
    await expect(sites).toContainText('development')
    await expect(sites).toContainText("with Attu's code")
  })

  await test.step('the panel on the story shows its testing and what is left', async () => {
    const panel = await (await request.post(`${API_URL}/jira-app/issue-panel`, {
      data: { issue_key: 'JAPP-7', project_key: 'JAPP' }, headers: await fromJira('e2e-site'),
    })).json()
    expect(panel.stories).toHaveLength(1)
    expect(panel.stories[0].title).toBe('Customers can renew an expired ID')
    expect(panel.stories[0].left[0]).toContain('No risks linked yet')
    const other = await (await request.post(`${API_URL}/jira-app/issue-panel`, { data: { issue_key: 'JAPP-8' }, headers: await fromJira('e2e-site') })).json()
    expect(other.projects.map((p: { name: string }) => p.name)).toEqual(['AddisVerify Jira app'])
  })

  await test.step('unlink the site in Mirmera: its issues stop showing Mirmera', async () => {
    await page.getByRole('button', { name: 'Unlink' }).click()
    await page.getByRole('button', { name: 'Yes, delete' }).click()
    await expect(page.getByRole('region', { name: 'Linked Jira sites' })).toContainText('None yet.')
    const after = await request.post(`${API_URL}/jira-app/issue-panel`, { data: { issue_key: 'JAPP-7' }, headers: await fromJira('e2e-site') })
    expect(after.status()).toBe(409)
  })
})
