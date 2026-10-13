import { API_URL, expect, test } from './fixtures'

// The project's test data in Settings: add a secret value for a field, import vouchers, see a secret stay hidden.
test('test data: add a secret value, import one-use vouchers, change and delete', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Test data')
  await page.goto(`/projects/${project.id}/settings`)
  const section = page.getByRole('region', { name: 'Test data' })

  await section.getByRole('button', { name: 'Add test data' }).click()
  const form = section.getByRole('form', { name: 'Add test data' })
  await form.getByLabel('Name').fill('valid national ID')
  await form.getByLabel('Value').fill('ET-4471-0093')
  await form.getByLabel("Another field's label").fill('National ID')
  await form.getByLabel("Another field's label").press('Enter')
  await form.getByLabel('Secret: encrypted, never shown again, never shown to the AI').check()
  await page.screenshot({ path: 'test-results/test-data-add.png' })
  await form.getByRole('button', { name: 'Add' }).click()

  const row = section.getByRole('listitem', { name: 'valid national ID' })
  await expect(row).toContainText('••• (secret)')
  await expect(row).toContainText('Fills: National ID')
  await expect(section).not.toContainText('ET-4471-0093')  // never shown again
  const stored = await (await request.get(`${API_URL}/projects/${project.id}/test-data`)).json()
  expect(stored[0].value).toBeNull()

  await section.getByRole('button', { name: 'Import CSV' }).click()
  await section.getByLabel(/CSV: name,value/).fill('name,value,labels,kind,once\nvoucher 1,V-1001,Voucher code,valid,yes\nvoucher 2,V-1002,Voucher code,valid,yes\nexpired ID,ET-0000-0001,National ID,invalid,no')
  await section.getByRole('button', { name: 'Import' }).click()
  await expect(section.getByRole('listitem', { name: 'voucher 1' })).toContainText('once · 2 left')
  await expect(section.getByRole('listitem', { name: 'expired ID' })).toContainText('invalid')

  // Change a row: the secret stays as it is unless retyped.
  await row.getByRole('button', { name: 'Change' }).click()
  const change = section.getByRole('form', { name: 'Change valid national ID' })
  await expect(change.getByLabel('Value')).toHaveAttribute('placeholder', 'Kept: type to replace it')
  await change.getByLabel('Note (optional)').fill("Abebe's test account")
  await change.getByRole('button', { name: 'Save' }).click()
  await expect(row).toContainText("Abebe's test account")
  await page.screenshot({ path: 'test-results/test-data.png', fullPage: true })

  await section.getByRole('listitem', { name: 'voucher 2' }).getByRole('button', { name: 'Delete' }).click()
  await expect(section.getByRole('listitem', { name: 'voucher 2' })).toHaveCount(0)
  await expect(section.getByRole('listitem', { name: 'voucher 1' })).toContainText('once · 1 left')
})
