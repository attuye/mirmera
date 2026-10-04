import { API_URL, expect, test } from './fixtures'

test('heuristics library: find one, add it to a charter, see it in the session as a test idea', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Heuristics')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { default_tester: 'Attu' } })
  await api.charter(project.id, 'Explore the expiry date field with odd dates')

  await test.step('the library: groups, search, filter', async () => {
    await page.goto(`/projects/${project.id}/dashboard`)
    await page.getByRole('navigation', { name: 'Project pages' }).getByRole('link', { name: 'Heuristics' }).click()
    await expect(page.getByRole('region', { name: 'Oracles' })).toContainText('FEW HICCUPPS')
    await expect(page.getByRole('region', { name: 'Product elements' })).toContainText('SFDIPOT')
    await page.getByLabel('Search heuristics').fill('screen reader')
    await expect(page.getByRole('article', { name: 'Personas' })).toBeVisible()
    await expect(page.getByRole('article', { name: 'Claims' })).toHaveCount(0)
    await page.getByLabel('Search heuristics').fill('')
    await page.getByRole('group', { name: 'Group' }).getByRole('button', { name: 'Techniques' }).click()
    await expect(page.getByRole('region', { name: 'Oracles' })).toHaveCount(0)
  })

  await test.step('add Goldilocks to the charter', async () => {
    const card = page.getByRole('article', { name: 'Goldilocks' })
    await card.getByLabel('Charter for Goldilocks').selectOption({ label: 'Explore the expiry date field with odd dates' })
    await card.getByRole('button', { name: 'Add' }).click()
    await expect(page.getByRole('status')).toContainText('it comes back as a test idea')
    await expect(card.getByLabel('Charter for Goldilocks').locator('option', { hasText: '✓ Explore the expiry' })).toHaveCount(1)
  })

  await test.step('the charter shows it, and its session suggests it until a note uses it', async () => {
    await page.getByRole('link', { name: 'Charters', exact: true }).click()
    const charter = page.getByRole('region', { name: 'Your charters' }).getByRole('listitem').first()
    await expect(charter.getByRole('link', { name: 'Goldilocks' })).toBeVisible()
    await charter.click()
    await expect(page.getByLabel('Heuristics for this charter').getByRole('link', { name: 'Goldilocks' })).toBeVisible()
    const idea = page.getByRole('region', { name: 'Test ideas' }).getByRole('article', { name: 'Try: Goldilocks' })
    await expect(idea).toContainText('What is the smallest and largest value allowed?')
    await idea.getByRole('button', { name: 'Write a note' }).click()
    await expect(page.getByRole('textbox', { name: 'What are you observing?' })).toHaveValue('Tried Goldilocks: ')
    await idea.getByRole('link', { name: 'Chosen for this charter' }).click()
    await expect(page).toHaveURL(/\/heuristics#goldilocks$/)
    await expect(page.getByRole('article', { name: 'Goldilocks' })).toBeInViewport()
  })
})
