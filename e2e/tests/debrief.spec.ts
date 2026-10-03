import { expect, test } from './fixtures'

test('PROOF debrief: review the draft, add feelings, turn outlook and risks into real work', async ({ page, api }) => {
  // Its own project: promoting a risk here must not take R1 from the risks spec.
  const project = await api.newProject('AddisVerify Liveness')
  const mission = 'Explore selfie liveness with printed photos and screens'
  const charter = await api.charter(project.id, mission)
  const session = await api.session(charter.id)
  await api.note(session.id, 'test', 'Held a printed photo up to the camera')
  await api.note(session.id, 'setup', 'Camera permission prompt blocked the page')
  await api.note(session.id, 'question', 'Is a phone screen replay in scope?')
  await api.bug(session.id, 'Printed photo passes liveness check')

  await test.step('ending the session opens the drafted debrief', async () => {
    await page.goto(`/projects/${project.id}/sessions/${session.id}`)
    await page.getByRole('button', { name: 'End Session' }).click()
    await expect(page).toHaveURL(new RegExp(`/sessions/${session.id}/debrief$`))
    await expect(page.getByText('AI draft — review before saving')).toBeVisible()
    await expect(page.getByText(mission, { exact: true })).toBeVisible()
    await expect(page.getByText('Charter partly fulfilled')).toBeVisible()
    await expect(page.getByRole('img', { name: 'Time split' })).toBeVisible()
  })

  await test.step('each PROOF section is pre-filled from the log, except Feelings', async () => {
    await expect(page.getByRole('textbox', { name: 'Past', exact: true })).toHaveValue(/Mock debrief for charter/)
    await expect(page.getByRole('textbox', { name: 'Results', exact: true })).toHaveValue(/Printed photo passes liveness check/)
    await expect(page.getByRole('textbox', { name: 'Obstacles', exact: true })).toHaveValue('Camera permission prompt blocked the page')
    await expect(page.getByRole('textbox', { name: 'Open questions', exact: true })).toHaveValue('Is a phone screen replay in scope?')
    await expect(page.getByRole('textbox', { name: 'Feelings', exact: true })).toHaveValue('')
  })

  await test.step('the tester edits results, writes feelings, and saves', async () => {
    await page.getByRole('textbox', { name: 'Results', exact: true }).fill('Printed photo passes liveness check\nScreen replay is rejected')
    await page.getByRole('textbox', { name: 'Feelings', exact: true }).fill('Confident on photos, rushed on screens')
    await expect(page.getByText('Unsaved changes')).toBeVisible()
    await page.getByRole('button', { name: 'Save debrief' }).click()
    await expect(page.getByRole('status')).toHaveText('Saved ✓')
  })

  await test.step('an outlook item becomes a follow-up charter, once', async () => {
    const outlook = page.getByRole('region', { name: 'Outlook' })
    await outlook.getByRole('button', { name: 'Save as charter' }).click()
    await expect(outlook.getByRole('link', { name: /Saved as charter/ })).toBeVisible()
    await expect(outlook.getByRole('button', { name: 'Save as charter' })).toHaveCount(0)
    const followUps = (await api.charters(project.id)).filter((c) => c.source === 'follow-up')
    expect(followUps).toHaveLength(1)
    expect(followUps[0].mission_statement).toContain(mission.slice(0, 40))
  })

  await test.step('an observed risk is scored and added to the risk list', async () => {
    const outlook = page.getByRole('region', { name: 'Outlook' })
    await outlook.getByRole('button', { name: 'Add as risk…' }).click()
    await outlook.getByLabel('Risk title').fill('Liveness accepts printed photos')
    await outlook.getByLabel('Quality criterion').selectOption('security')
    await outlook.getByLabel('Impact').selectOption('5')
    await outlook.getByLabel('Likelihood').selectOption('4')
    await expect(outlook.getByText('score 20')).toBeVisible()
    await outlook.getByRole('button', { name: 'Add to risk list' }).click()
    await expect(outlook.getByRole('link', { name: /Added to risks/ })).toBeVisible()
    const risk = (await api.risks(project.id)).find((r) => r.title === 'Liveness accepts printed photos')
    expect(risk).toMatchObject({ score: 20, status: 'accepted', source: 'session' })
  })

  await test.step('after a reload, edits and links are still there', async () => {
    await page.reload()
    await expect(page.getByRole('textbox', { name: 'Feelings', exact: true })).toHaveValue('Confident on photos, rushed on screens')
    await expect(page.getByRole('textbox', { name: 'Results', exact: true })).toHaveValue(/Screen replay is rejected/)
    await expect(page.getByRole('link', { name: /Saved as charter/ })).toBeVisible()
    await expect(page.getByRole('link', { name: /Added to risks/ })).toBeVisible()
  })
})
