import { expect, test } from './fixtures'

// Runs after risks.spec.ts (alphabetical order, one worker), so the risk added
// here never takes the R1/R2 refs that spec expects.
test('feedback loop: a session suggests a re-score, the tester decides, the risk keeps the history', async ({ page, api }) => {
  const product = await api.product()
  const title = 'Liveness check accepts printed photos'
  const risk = await api.risk(product.id, title, 5, 2)
  const charter = await api.charter(product.id, 'Explore selfie liveness with printed photos', [risk.id])
  const session = await api.session(charter.id)
  await api.note(session.id, 'test', 'Held a printed photo up to the camera')
  await api.bug(session.id, 'Printed photo passes liveness')

  const scores = page.getByRole('region', { name: 'Risk scores' })
  const card = scores.getByRole('listitem', { name: `${risk.ref} ${title}` })

  await test.step('end the session; the debrief offers suggestions and flags unreviewed bugs', async () => {
    await page.goto(`/sessions/${session.id}`)
    await page.getByRole('button', { name: 'End Session' }).click()
    await expect(page).toHaveURL(new RegExp(`/sessions/${session.id}/debrief$`))
    await expect(scores.getByText(/1 bug from this session not reviewed yet/)).toBeVisible()
  })

  await test.step('the suggestion shows the score change, the reason and the quoted evidence', async () => {
    await scores.getByRole('button', { name: 'Suggest risk updates' }).click()
    await expect(card).toContainText('I5×L2 = 10')
    await expect(card).toContainText('I5×L3 = 15')
    await expect(card).toContainText('- [unreviewed, medium] Printed photo passes liveness')
  })

  await test.step('the tester accepts with their own likelihood', async () => {
    await card.getByLabel('Likelihood').selectOption('4')
    await card.getByLabel('Your note (optional)').fill('Reproduced with two different photos')
    await card.getByRole('button', { name: 'Accept my score' }).click()
    await expect(card.getByRole('status')).toContainText('Accepted: now I5×L4 = 20')
    await expect(scores.getByRole('button', { name: 'Ask again' })).toBeVisible()
  })

  await test.step('the risk has the new score and the decision in its history', async () => {
    await page.goto('/risks')
    const riskCard = page.getByRole('article', { name: `${risk.ref} ${title}` })
    await expect(riskCard.getByText(`${risk.ref} · 20`)).toBeVisible()
    await expect(riskCard.getByText(/after a session \(adjusted by tester\)/)).toBeVisible()
    await riskCard.getByRole('button', { name: 'History' }).click()
    const history = riskCard.getByRole('list', { name: 'Score history' })
    await expect(history.getByRole('listitem')).toHaveCount(1)
    await expect(history).toContainText('Accepted')
    await expect(history).toContainText('Your note: Reproduced with two different photos')
    await expect(history.getByRole('link', { name: 'session' })).toHaveAttribute('href', `/sessions/${session.id}/debrief`)
  })

  await test.step('a manual re-score is added to the same history', async () => {
    const riskCard = page.getByRole('article', { name: `${risk.ref} ${title}` })
    await riskCard.getByLabel('Impact').selectOption('4')
    const history = riskCard.getByRole('list', { name: 'Score history' })
    await expect(history.getByRole('listitem')).toHaveCount(2)
    await expect(history.getByRole('listitem').first()).toContainText('Re-scored by you')
  })
})
