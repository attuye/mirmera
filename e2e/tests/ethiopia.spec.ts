import { API_URL, expect, test } from './fixtures'

// Settings → Testing: "Users in Ethiopia" turns on the Ethiopia pack for the project's runs.
test('settings: users in Ethiopia is saved with the project', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Ethiopia')
  await page.goto(`/projects/${project.id}/settings`)
  const box = page.getByRole('checkbox', { name: /Users in Ethiopia/ })
  await expect(box).not.toBeChecked()
  await box.check()
  await page.getByRole('button', { name: 'Save settings' }).click()
  await expect.poll(async () => (await (await request.get(`${API_URL}/projects/${project.id}`)).json()).ethiopia).toBe(true)
  await page.reload()
  await expect(page.getByRole('checkbox', { name: /Users in Ethiopia/ })).toBeChecked()
  await page.screenshot({ path: 'test-results/ethiopia-setting.png' })
})
