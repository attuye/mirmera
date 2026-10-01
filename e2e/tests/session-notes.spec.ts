import { expect, test } from './fixtures'

// Guards the fix for notes that were silently lost when a tester logged entries
// quickly, one after another (about 1 in 15 before the fix).
test('notes logged in quick succession are all saved', async ({ page, api }) => {
  const product = await api.product()
  const charter = await api.charter(product.id, 'Explore fast note-taking')
  const session = await api.session(charter.id)
  await page.goto(`/sessions/${session.id}`)

  const box = page.getByPlaceholder('What are you observing?')
  const entries = Array.from({ length: 60 }, (_, i) => `Observation ${i + 1}`)
  for (const entry of entries) {
    await box.fill(entry)
    await page.getByRole('button', { name: 'Log Note' }).click()
    await expect(page.getByText(entry, { exact: true })).toBeVisible()
  }

  const saved = (await api.notes(session.id)).map((n) => n.content)
  expect(saved).toEqual(entries)
})

test('Ctrl+Enter logs the note', async ({ page, api }) => {
  const product = await api.product()
  const session = await api.session((await api.charter(product.id, 'Explore keyboard logging')).id)
  await page.goto(`/sessions/${session.id}`)

  const box = page.getByPlaceholder('What are you observing?')
  await box.fill('Logged from the keyboard')
  await box.press('Control+Enter')
  await expect(page.getByText('Logged from the keyboard', { exact: true })).toBeVisible()
  await expect(box).toHaveValue('')
})

test('a note that fails to save stays in the box with a reason', async ({ page, api }) => {
  const product = await api.product()
  const session = await api.session((await api.charter(product.id, 'Explore failed saves')).id)
  await page.goto(`/sessions/${session.id}`)

  // Simulate the backend being unreachable for saves only.
  await page.route('**/notes', (route) => (route.request().method() === 'POST' ? route.abort() : route.continue()))
  const box = page.getByPlaceholder('What are you observing?')
  await box.fill('Important observation')
  await page.getByRole('button', { name: 'Log Note' }).click()

  await expect(page.getByRole('alert')).toContainText('Not saved')
  await expect(page.getByRole('alert')).toContainText('Your text is still in the box')
  await expect(box).toHaveValue('Important observation')

  await test.step('retry once the backend is back', async () => {
    await page.unroute('**/notes')
    await page.getByRole('button', { name: 'Log Note' }).click()
    await expect(page.getByText('Important observation', { exact: true })).toBeVisible()
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(box).toHaveValue('')
  })
})
