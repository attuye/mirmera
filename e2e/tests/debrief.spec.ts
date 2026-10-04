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

  await test.step('the report: summary first, then the full story folded', async () => {
    await page.evaluate(() => localStorage.removeItem('mirmera.report.view'))
    await page.getByRole('link', { name: 'Report', exact: true }).click()
    const summary = page.getByRole('region', { name: 'Summary' })
    await expect(summary).toContainText('Sessions1')
    await expect(summary).toContainText('1 to review')  // the logged bug isn't reviewed yet

    // Summary: at a glance, with where to act, and the charts.
    const glance = page.getByRole('region', { name: 'At a glance' })
    const risk = glance.getByRole('listitem', { name: '1 high risk not tested' })
    await expect(risk).toContainText('R1 Liveness accepts printed photos (score 20)')
    await expect(glance.getByRole('listitem', { name: '1 charter partly fulfilled' })).toBeVisible()
    await expect(glance.getByRole('listitem', { name: '1 report waiting for review' })).toBeVisible()
    await expect(page.getByRole('figure', { name: 'Accepted risks tested' })).toContainText('0 of 1')
    await expect(page.getByRole('article', { name: 'Report' })).toHaveCount(0)

    // Full: the three parts, folded; "How good the testing was" open with its gaps.
    await page.getByRole('group', { name: 'View' }).getByRole('button', { name: 'Full' }).click()
    const report = page.getByRole('article', { name: 'Report' })
    const testing = report.getByRole('region', { name: '2. The testing: how we tested' })
    await expect(testing.getByRole('button', { name: /2\. The testing/ })).toHaveAttribute('aria-expanded', 'false')
    await expect(testing).toContainText('1 session')
    await testing.getByRole('button', { name: /2\. The testing/ }).click()
    await expect(testing.getByRole('row', { name: new RegExp(mission) })).toContainText('Partly fulfilled')
    const quality = report.getByRole('region', { name: '3. How good the testing was' })
    await expect(quality).toContainText('High risk not tested: R1 Liveness accepts printed photos (score 20)')
    await expect(quality).not.toContainText('Camera permission prompt blocked the page')  // folded
    await quality.getByRole('button', { name: /^Obstacles/ }).click()
    await expect(quality).toContainText('Camera permission prompt blocked the page')
    await quality.getByRole('button', { name: /^How the testers felt/ }).click()
    await expect(quality).toContainText('Confident on photos, rushed on screens')
    await page.reload()
    await expect(page.getByRole('group', { name: 'View' }).getByRole('button', { name: 'Full' })).toHaveAttribute('aria-pressed', 'true')

    // A gap's link opens where to act on it.
    await risk.getByRole('link', { name: 'Make a charter' }).click()
    await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/risks$`))
    await page.goBack()

    // Periods, and exports that follow the view.
    await page.getByRole('button', { name: 'Last 7 days' }).click()
    await expect(page.getByRole('status', { name: 'Report period' })).toContainText(' to ')
    await page.getByRole('button', { name: 'Custom…' }).click()
    await page.getByLabel('From').fill('2020-01-01')
    await page.getByLabel('To').fill('2020-01-31')
    await expect(summary).toContainText('Sessions0')
    await expect(page.getByRole('region', { name: 'At a glance' })).not.toContainText('charter partly fulfilled')  // no session then
    await expect(page.getByRole('region', { name: 'At a glance' })).toContainText('1 high risk not tested')  // still untested

    const md = await page.request.get((await page.getByRole('link', { name: 'Download Markdown' }).getAttribute('href'))!)
    expect(md.headers()['content-disposition']).toContain('test-report-2020-01-01-to-2020-01-31.md')
    expect(await md.text()).toContain('# Test report: AddisVerify Liveness')
    await page.getByRole('button', { name: 'All time' }).click()
    const printable = await page.request.get((await page.getByRole('link', { name: 'Printable page' }).getAttribute('href'))!)
    expect(await printable.text()).toContain('Confident on photos, rushed on screens')
    await page.getByRole('group', { name: 'View' }).getByRole('button', { name: 'Summary' }).click()
    const short = await page.request.get((await page.getByRole('link', { name: 'Download Markdown' }).getAttribute('href'))!)
    expect(short.headers()['content-disposition']).toContain('test-report-summary.md')
    expect(await short.text()).not.toContain('Confident on photos')
  })
})
