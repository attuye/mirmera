import { expect, test } from './fixtures'

// Not a bug, and why: the draft moves to Dismissed, Settings lists it, and Undo brings it back.
test('dismiss a draft with a reason, see it in Settings, undo it', async ({ page, api }) => {
  const project = await api.newProject('AddisVerify Dismiss')
  const charter = await api.charter(project.id, 'Explore the confirmation page')
  const session = await api.session(charter.id)
  const title = 'Confirmation page is blank when opened directly'
  await api.bug(session.id, title)

  await page.goto(`/projects/${project.id}/bugs`)
  const row = page.getByRole('article', { name: title })
  await row.getByRole('button', { name: new RegExp(title) }).click()
  await row.getByRole('button', { name: 'Dismiss…' }).click()
  const form = row.getByRole('form', { name: 'Dismiss' })
  await form.getByLabel('Why').selectOption({ label: 'Works as intended' })
  await form.getByLabel('Note for the team and Mirmera (optional)').fill('It is only reached from the form, on purpose.')
  await page.screenshot({ path: 'test-results/dismiss.png' })
  await form.getByRole('button', { name: 'Dismiss' }).click()

  // Out of All, into Dismissed.
  await expect(page.getByRole('tab', { name: 'All (0)' })).toBeVisible()
  await page.getByRole('tab', { name: 'Dismissed (1)' }).click()
  const dismissed = page.getByRole('article', { name: title })
  const header = dismissed.getByRole('button', { name: new RegExp(title) })
  if (await header.getAttribute('aria-expanded') !== 'true') await header.click()
  await expect(dismissed.getByRole('status')).toContainText('Works as intended')
  await expect(dismissed.getByRole('status')).toContainText("Mirmera won't draft this problem again in this project.")

  await page.goto(`/projects/${project.id}/settings`)
  const list = page.getByRole('region', { name: 'Dismissed problems' })
  await expect(list.getByRole('listitem', { name: title })).toContainText('Works as intended')
  await expect(list).toContainText('"It is only reached from the form, on purpose."')

  await page.goto(`/projects/${project.id}/bugs`)
  await page.getByRole('tab', { name: 'Dismissed (1)' }).click()
  const again = page.getByRole('article', { name: title })
  const head = again.getByRole('button', { name: new RegExp(title) })
  if (await head.getAttribute('aria-expanded') !== 'true') await head.click()
  await again.getByRole('button', { name: 'Undo: a draft again' }).click()
  await expect(page.getByRole('tab', { name: 'Needs review (1)' })).toBeVisible()
})
