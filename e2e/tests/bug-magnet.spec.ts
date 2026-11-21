import { expect, test } from './fixtures'

// Bug Magnet in a session: tricky values to copy into the app under test, and a note started for each try.
test('bug magnet: copy a tricky value, then log what the app did with it', async ({ page, api, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  const project = await api.project()
  const session = await api.session((await api.charter(project.id, 'Explore sign-up with unusual emails and names')).id)
  await page.goto(`/projects/${project.id}/sessions/${session.id}`)
  await page.getByRole('tab', { name: 'Bug Magnet' }).click()
  const magnet = page.getByRole('region', { name: 'Bug Magnet' })
  await expect(magnet.getByRole('list', { name: 'Names' })).toContainText('Zoë Saldaña')  // open first

  await magnet.getByRole('button', { name: /Emails/ }).click()
  const plus = magnet.getByRole('list', { name: 'Emails' }).getByRole('listitem', { name: 'a plus tag (valid)' })
  await plus.getByRole('button', { name: 'Copy' }).click()
  await expect(plus.getByRole('button', { name: 'Copied' })).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('name+test@example.com')

  await magnet.getByRole('button', { name: /Text and Unicode/ }).click()
  const zw = magnet.getByRole('listitem', { name: /zero-width space/ })
  await expect(zw).toContainText('a⟨zero-width space⟩b')  // what can't be seen is named
  await zw.getByRole('button', { name: 'Copy' }).click()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('a​b')  // and copied as it is

  await magnet.getByRole('button', { name: /Emails/ }).click()  // one list open at a time
  await plus.getByRole('button', { name: 'Note' }).click()
  const box = page.getByPlaceholder('What are you observing?')
  await expect(box).toHaveValue('Tried a plus tag (valid) (Bug Magnet): ')
  await expect(box).toBeFocused()
  await box.pressSequentially('Email field, the app refused it as invalid')
  await page.getByRole('button', { name: 'Log Note' }).click()
  await expect(page.getByText('Tried a plus tag (valid) (Bug Magnet): Email field, the app refused it as invalid')).toBeVisible()
  if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/bug-magnet.png` })
})
