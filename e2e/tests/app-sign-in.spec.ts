import { API_URL, expect, test } from './fixtures'

// Signing in to the app under test: set once in the project's Settings, tried in a headless
// browser; the password is never shown again.
test('sign in to the app under test: save, try, and the password stays hidden', async ({ page, api, request }) => {
  const project = await api.newProject('Behind a sign-in')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { app_url: 'http://localhost:5190' } })
  await page.goto(`/projects/${project.id}/settings`)
  const box = page.getByRole('region', { name: 'Signing in to the app' })
  await box.getByLabel('Sign-in page').fill('/login')
  await box.getByLabel('Username', { exact: true }).fill('tester@example.com')
  await box.getByLabel('Password', { exact: true }).fill('e2e-pass')
  await box.getByLabel('Words that show it worked').fill('Sign out')
  await box.getByRole('button', { name: 'Save sign-in' }).click()
  await expect(box.getByRole('status')).toHaveText('Saved. Try it to be sure.')
  await expect(box.getByLabel('Password', { exact: true })).toHaveValue('')
  await expect(box.getByLabel('Password', { exact: true })).toHaveAttribute('placeholder', 'Saved: type to replace')

  await box.getByRole('button', { name: 'Try sign-in' }).click()
  const result = box.getByRole('region', { name: 'Sign-in result' })
  await expect(result).toContainText('Signed in.', { timeout: 30_000 })
  await expect(result).toContainText('signed in as tester@example.com ("Sign out" is on the page)')
  await expect(result).toContainText('http://localhost:5190/account')
  await expect(result.getByRole('img', { name: 'The app after signing in' })).toBeVisible()
  if (process.env.SHOTS) await box.screenshot({ path: `${process.env.SHOTS}/sign-in.png` })

  const saved = await (await request.get(`${API_URL}/projects/${project.id}/sign-in`)).text()
  expect(saved).not.toContain('e2e-pass')  // never sent back

  await box.getByLabel('Password', { exact: true }).fill('wrong')
  await box.getByRole('button', { name: 'Save sign-in' }).click()
  await expect(box.getByRole('status')).toHaveText('Saved. Try it to be sure.')
  await box.getByRole('button', { name: 'Try sign-in' }).click()
  await expect(result).toContainText("Couldn't sign in.", { timeout: 30_000 })
  await expect(result).toContainText('Wrong email or password.')
})
