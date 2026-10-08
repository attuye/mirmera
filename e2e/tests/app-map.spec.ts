import { API_URL, expect, test } from './fixtures'

// Survey the app: Mirmera opens every page it can reach and reads its forms, without
// submitting anything, and the Coverage page shows which pages no session has opened.
test('the app map shows what there is to test, and what nobody has tested', async ({ page, api, request }) => {
  const project = await api.newProject('Map Survey')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { app_url: 'http://localhost:5190' } })
  await page.goto(`/projects/${project.id}/coverage`)
  const map = page.getByRole('region', { name: 'App map' })
  await expect(map).toContainText('It never submits anything.')
  await map.getByRole('button', { name: 'Survey the app' }).click()
  const pages = map.getByRole('list', { name: 'Pages in the app' })
  await expect(pages.getByRole('listitem', { name: '/' })).toContainText('Upload your ID', { timeout: 30_000 })
  await expect(pages.getByRole('listitem', { name: '/' })).toContainText('Search documents')
  await expect(pages.getByRole('listitem', { name: '/' })).toContainText('never tested')
  await expect(map).toContainText('never opened in a session')
  await expect(map.getByRole('button', { name: 'Survey again' })).toBeVisible()
})
