import { expect, test } from './fixtures'

test('plan from risks: model the product, review risks, save a charter', async ({ page, api }) => {
  const project = await api.project()
  await page.goto(`/projects/${project.id}/risks`)

  await test.step('page starts empty', async () => {
    await expect(page.getByRole('heading', { name: 'Risks', level: 1 })).toBeVisible()
    await expect(page.getByText('No risks yet.')).toBeVisible()
  })

  await test.step('save the testing mission', async () => {
    await page.getByLabel(/Testing mission/).fill('Find release blockers in the new ID document upload flow')
    const save = page.getByRole('button', { name: 'Save', exact: true })
    await save.click()
    await expect(save).toBeDisabled()
  })

  await test.step('add an artifact', async () => {
    await page.getByLabel('Type').selectOption('diff')
    await page.getByLabel('Name').fill('pr-731-upload.diff')
    await page.getByLabel('Content').fill('+++ b/kyc/upload.py\n+def verify_document(img): ...\n+def store_selfie(user, img): ...')
    await page.getByRole('button', { name: 'Add artifact' }).click()
    await expect(page.getByText('pr-731-upload.diff')).toBeVisible()
  })

  await test.step('build the product model', async () => {
    await page.getByRole('button', { name: 'Build product model' }).click()
    await expect(page.getByText('Questions for the team')).toBeVisible()
    await expect(page.getByText('verify_document', { exact: true })).toBeVisible()
  })

  await test.step('analyse risks: all start in review', async () => {
    await page.getByRole('button', { name: 'Analyse risks' }).click()
    await expect(page.getByText('Needs review (3)')).toBeVisible()
    await expect(page.getByText('Accepted (0)')).toBeVisible()
  })

  const card = (ref: string) => page.getByRole('article', { name: new RegExp(`^${ref} `) })

  await test.step('accept, re-score and annotate', async () => {
    await card('R1').getByRole('button', { name: 'Accept' }).click()
    await expect(page.getByText('Accepted (1)')).toBeVisible()
    await card('R2').getByRole('button', { name: 'Accept' }).click()
    await expect(page.getByText('Accepted (2)')).toBeVisible()

    await card('R2').getByLabel('Likelihood').selectOption('4')
    await expect(card('R2').getByText(/Re-scored I5xL2 -> I5xL4/)).toBeVisible()
    await expect(card('R2').getByText('R2 · 20')).toBeVisible()

    const notes = card('R1').getByLabel(/Your notes/)
    await notes.fill('Last release broke selfie storage on Android')
    await notes.blur()
    await expect(card('R1').getByText(/Notes updated/)).toBeVisible()
  })

  await test.step('retire a risk, then find it under retired', async () => {
    await card('R3').getByRole('button', { name: 'Retire' }).click()
    await expect(page.getByText('Needs review (0)')).toBeVisible()
    await page.getByRole('button', { name: /Show retired risks \(1\)/ }).click()
    await expect(card('R3').getByRole('button', { name: 'Reopen' })).toBeVisible()
  })

  await test.step('add a risk by hand: accepted straight away', async () => {
    await page.getByRole('button', { name: 'Add a risk yourself' }).click()
    await page.getByLabel('Risk', { exact: true }).fill('Expired ID accepted')
    await page.getByLabel(/What fails/).fill('An expired national ID passes verification and the user is onboarded')
    await page.getByLabel('Quality criterion').selectOption('security')
    await page.getByRole('button', { name: 'Add risk' }).click()
    await expect(page.getByText('Accepted (3)')).toBeVisible()
    await expect(card('R4').getByText('Tester', { exact: true })).toBeVisible()
  })

  await test.step('the matrix shows accepted risks and selects their card', async () => {
    const chip = page.getByRole('button', { name: /^R4: / })
    await chip.click()
    await expect(card('R4')).toBeInViewport()
  })

  await test.step('draft charters from accepted risks and save one', async () => {
    await page.getByRole('button', { name: 'Draft charters' }).click()
    const save = page.getByRole('button', { name: 'Save charter' }).first()
    await save.click()
    await expect(page.getByRole('link', { name: 'Saved. Start it from Charters' })).toBeVisible()
  })

  await test.step('the saved charter is on the Charters page', async () => {
    await page.getByRole('link', { name: 'Charters', exact: true }).click()
    await expect(page.getByText(/^Explore .* to discover/).first()).toBeVisible()
  })

  await test.step('everything survives a reload', async () => {
    await page.goto(`/projects/${project.id}/risks`)
    await expect(page.getByText('Accepted (3)')).toBeVisible()
    await expect(page.getByText('Questions for the team')).toBeVisible()
  })
})
