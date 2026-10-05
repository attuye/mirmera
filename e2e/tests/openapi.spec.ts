import { API_URL, expect, test } from './fixtures'

const TARGET = 'http://localhost:5190'

test('OpenAPI: import the spec from its address, pick an endpoint, send it, see it counted as tried', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify OpenAPI')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { app_url: TARGET } })
  const charter = await api.charter(project.id, 'Explore the target API from its spec')
  const session = await api.session(charter.id)
  await page.goto(`/projects/${project.id}/sessions/${session.id}`)
  await page.getByRole('tab', { name: 'API' }).click()
  const panel = page.getByRole('region', { name: 'API', exact: true })

  await test.step('import the spec from the app', async () => {
    await panel.getByRole('button', { name: 'From OpenAPI' }).click()
    await panel.getByLabel('Spec address').fill(`${TARGET}/openapi.json`)
    await panel.getByRole('button', { name: 'Import', exact: true }).click()
    const endpoints = panel.getByRole('group', { name: 'OpenAPI endpoints' })
    await expect(endpoints).toContainText('Target API')
    await expect(endpoints).toContainText('0 of 3 endpoints tried in this project')
    await expect(endpoints.getByLabel('Send to')).toHaveValue(TARGET)
  })

  await test.step('a POST comes with an example body', async () => {
    await panel.getByRole('listitem', { name: 'POST /api/documents' }).click()
    await expect(panel.getByLabel('Method')).toHaveValue('POST')
    await expect(panel.getByLabel('URL', { exact: true })).toHaveValue(`${TARGET}/api/documents`)
    await expect(panel.getByLabel('Body')).toHaveValue(/"number": "EP1234567"/)
  })

  await test.step('an endpoint that needs a token says so; with the token it answers', async () => {
    await panel.getByRole('button', { name: 'From OpenAPI' }).click()
    await panel.getByLabel('Search endpoints').fill('signed in')
    await panel.getByRole('listitem', { name: 'GET /api/profile' }).click()
    await expect(panel.getByRole('status')).toContainText('Needs bearer token')
    await panel.getByRole('button', { name: /Session headers/ }).click()
    await panel.getByRole('button', { name: '+ Header' }).last().click()
    await panel.getByLabel('Session header 1 name').fill('Authorization')
    await panel.getByLabel('Session header 1 value').fill('Bearer demo-token')
    await panel.getByRole('button', { name: 'Send', exact: true }).click()
    await expect(panel.getByRole('region', { name: 'Response' })).toContainText('200 OK')
  })

  await test.step('the endpoint now counts as tried', async () => {
    await panel.getByRole('button', { name: 'From OpenAPI' }).click()
    const endpoints = panel.getByRole('group', { name: 'OpenAPI endpoints' })
    await expect(endpoints).toContainText('1 of 3 endpoints tried in this project')
    await expect(endpoints.getByRole('listitem', { name: 'GET /api/profile' })).toContainText('tried 1× · 200')
  })
})
