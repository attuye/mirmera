import { API_URL, expect, test } from './fixtures'

const TOKEN = 'github_pat_11E2E_not_a_real_token_k3y9'

test('workspace keys: set a GitHub token and a Claude key, never see them again, the project uses them', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Keys')

  await test.step('open the keys from the project settings', async () => {
    await page.goto(`/projects/${project.id}/settings`)
    await page.getByRole('link', { name: 'Workspace keys →' }).click()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('AddisVerify: keys')
    await expect(page.getByRole('region', { name: 'GitHub' })).toContainText('Not set')
  })

  await test.step('enter keys and save: only their last characters come back', async () => {
    await page.getByLabel('GitHub token', { exact: true }).fill(TOKEN)
    await page.getByLabel('Jira site').fill('https://acme.atlassian.net')
    await page.getByLabel('Claude API key', { exact: true }).fill('sk-ant-e2e-workspace-key-abcd')
    await page.getByRole('button', { name: 'Save keys' }).click()
    await expect(page.getByRole('status')).toHaveText('Keys saved.')
    await expect(page.getByRole('region', { name: 'GitHub' })).toContainText('Set …k3y9 · Attu')
    await expect(page.getByRole('region', { name: 'Jira' })).toContainText('Set https://acme.atlassian.net')
    await expect(page.getByLabel('GitHub token', { exact: true })).toHaveValue('')
    await page.reload()
    await expect(page.getByRole('region', { name: 'GitHub' })).toContainText('Set …k3y9')
    expect(await page.content()).not.toContain(TOKEN)
    const listed = await (await request.get(`${API_URL}/workspaces/${await api.workspaceId()}/keys`)).text()
    expect(listed).not.toContain(TOKEN)
  })

  await test.step("the project's AI settings see the workspace's Claude key", async () => {
    const ai = await (await request.get(`${API_URL}/ai?project_id=${project.id}`)).json()
    expect(ai.providers.find((p: { name: string }) => p.name === 'claude').configured).toBe(true)
  })

  await test.step('remove a key', async () => {
    await page.getByRole('button', { name: 'Remove GitHub token' }).click()
    await expect(page.getByRole('status')).toHaveText('GitHub token removed.')
    await expect(page.getByRole('region', { name: 'GitHub' })).toContainText('Not set')
    await page.getByRole('button', { name: 'Remove Claude API key' }).click()
    await expect(page.getByRole('region', { name: 'AI' })).not.toContainText('Set …')
  })
})
