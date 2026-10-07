import { test, expect } from './fixtures'

test('AI settings: check a model task by task, choose a provider per task', async ({ page, api }) => {
  const project = await api.newProject(`AI routing ${Date.now()}`)
  await page.goto(`/projects/${project.id}/settings`)
  const ai = page.getByRole('region', { name: 'AI' })

  await test.step('check the mock model: every task usable first time', async () => {
    await ai.getByLabel('Model to check').selectOption('mock')
    await ai.getByRole('button', { name: 'Check this model' }).click()
    await expect(ai.getByRole('status')).toContainText('mock: 14 OK, 0 needed a retry, 0 failed', { timeout: 30_000 })
    await expect(ai.getByRole('row', { name: 'Draft bug reports' })).toContainText('OK')
    await expect(ai.getByRole('row', { name: 'Draft bug reports' })).toContainText('(oracle: claims)')
  })

  await test.step('route one task to another provider, save, and it stays', async () => {
    await ai.getByLabel('Provider for Draft debriefs').selectOption('local')
    await page.getByRole('button', { name: 'Save settings' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible()
    await page.reload()
    await expect(ai.getByLabel('Provider for Draft debriefs')).toHaveValue('local')
    await expect(ai.getByLabel('Provider for Write regression tests')).toHaveValue('')  // the default
  })

  await test.step('use the checked model for the tasks it handled first time', async () => {
    await ai.getByLabel('Model to check').selectOption('mock')
    await ai.getByRole('button', { name: 'Check this model' }).click()
    await ai.getByRole('button', { name: 'Use mock for the 14 tasks it handled first time' }).click({ timeout: 30_000 })
    await expect(ai.getByLabel('Provider for Draft debriefs')).toHaveValue('mock')
    await page.getByRole('button', { name: 'Save settings' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible()
  })
})
