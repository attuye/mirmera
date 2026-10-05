import { API_URL, expect, test } from './fixtures'

const TARGET = 'http://localhost:5190'

test('API exploration: shared token, a variation without it, a curl that fails, logged as a bug', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify API')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { app_url: TARGET } })
  const charter = await api.charter(project.id, 'Explore the documents API with and without a token')
  const session = await api.session(charter.id)

  await page.goto(`/projects/${project.id}/sessions/${session.id}`)
  await page.getByRole('tablist', { name: 'Tools' }).getByRole('tab', { name: 'API' }).click()
  const panel = page.getByRole('region', { name: 'API', exact: true })

  await test.step('a token for the whole session, then a path on the app', async () => {
    await panel.getByRole('button', { name: /Session headers/ }).click()
    await panel.getByRole('button', { name: '+ Header' }).last().click()
    await panel.getByLabel('Session header 1 name').fill('Authorization')
    await panel.getByLabel('Session header 1 value').fill('Bearer demo-token')
    await panel.getByLabel('URL', { exact: true }).fill('/api/profile')
    await expect(panel.getByText(`On ${TARGET}`)).toBeVisible()
    await panel.getByRole('button', { name: 'Send', exact: true }).click()
    const response = panel.getByRole('region', { name: 'Response' })
    await expect(response).toContainText('200 OK')
    await expect(response.getByLabel('Response body')).toContainText('"name": "Abebe"')
  })

  await test.step('vary it: without auth', async () => {
    await panel.getByRole('button', { name: 'Without auth' }).click()
    await expect(panel.getByRole('region', { name: 'Response' })).toContainText('401 Unauthorized')
  })

  await test.step('paste a curl that fails, log it as a bug with the request attached', async () => {
    await panel.getByRole('button', { name: 'Paste curl' }).click()
    await panel.getByLabel('curl command').fill(`curl '${TARGET}/api/documents' \\\n  -H 'accept: application/json'`)
    await panel.getByRole('button', { name: 'Use this command' }).click()
    await expect(panel.getByLabel('URL', { exact: true })).toHaveValue(`${TARGET}/api/documents`)
    await panel.getByRole('button', { name: 'Send', exact: true }).click()
    await expect(panel.getByRole('region', { name: 'Response' })).toContainText('500 Internal Server Error')
    await expect(panel.getByLabel('Response body')).toContainText('storage unavailable')
    await panel.getByRole('button', { name: 'Log as bug' }).click()
    await expect(panel.getByRole('status')).toContainText('Bug logged with this request attached')
    await expect(page.getByText('GET /api/documents answered 500')).toBeVisible()  // in the session's notes
  })

  await test.step('three requests kept, the token nowhere in them', async () => {
    await expect(panel.getByRole('list', { name: 'Sent requests' }).getByRole('listitem')).toHaveCount(3)
    const history = await (await request.get(`${API_URL}/sessions/${session.id}/api/history`)).text()
    expect(history).not.toContain('demo-token')
    const bugs = await (await request.get(`${API_URL}/sessions/${session.id}/bugs`)).json()
    const evidence = await (await request.get(`${API_URL}/bugs/${bugs[0].id}/evidence`)).json()
    expect(evidence.map((e: { kind: string }) => e.kind)).toContain('api_call')
  })
})
