import { API_URL, expect, test } from './fixtures'

// MOCK_TRACKERS=true: the pull request comes from the in-memory GitHub; the mock AI suggests charters.
test('charters: suggest from a pull request, keep where it came from, start a session', async ({ page, api, request }) => {
  const project = await api.newProject('AddisVerify Charters')
  await request.patch(`${API_URL}/projects/${project.id}`, { data: { github_repo: 'addisverify/charters', default_tester: 'Attu' } })
  await page.goto(`/projects/${project.id}/charters/suggest`)
  await expect(page.getByRole('heading', { name: 'Charters', level: 1 })).toBeVisible()
  const suggestions = page.getByRole('region', { name: 'Suggestions' })
  const yours = page.getByRole('region', { name: 'Your charters' })

  await test.step('import a pull request and get suggestions for it', async () => {
    const panel = page.getByRole('region', { name: 'Suggest from a change' })
    await panel.getByRole('tab', { name: 'Import' }).click()
    await panel.getByLabel('Pull request').fill('42')
    await panel.getByRole('button', { name: 'Import and suggest' }).click()
    await expect(suggestions).toContainText('Suggestions for PR #42: Check passports against the MRZ (mock PR 42)')
    await expect(suggestions.getByRole('listitem').first()).toBeVisible()
  })

  await test.step('an accepted suggestion remembers the pull request', async () => {
    const first = suggestions.getByRole('listitem').first()
    const mission = (await first.getAttribute('aria-label'))!
    await first.getByRole('button', { name: 'Accept' }).click()
    const charter = yours.getByRole('listitem', { name: mission })
    await expect(charter.getByRole('link', { name: /from PR #42/ })).toHaveAttribute('href', 'https://mock-github.local/addisverify/charters/pull/42')
  })

  await test.step('suggestions are still there after leaving the page; one is dropped', async () => {
    const before = await suggestions.getByRole('listitem').count()
    await page.getByRole('navigation', { name: 'Project pages' }).getByRole('link', { name: 'Risks' }).click()
    await page.getByRole('navigation', { name: 'Project pages' }).getByRole('link', { name: 'Charters' }).click()
    await expect(suggestions).toContainText('Suggestions for PR #42')
    await expect(suggestions.getByRole('listitem')).toHaveCount(before)
    await suggestions.getByRole('listitem').first().getByRole('button', { name: 'Drop' }).click()
    await expect(suggestions.getByRole('listitem')).toHaveCount(before - 1)
    await page.reload()
    await expect(suggestions.getByRole('listitem')).toHaveCount(before - 1)
  })

  await test.step('pasted text gets a readable name, and earlier imports can be reused', async () => {
    const panel = page.getByRole('region', { name: 'Suggest from a change' })
    await panel.getByRole('tab', { name: 'Paste' }).click()
    await panel.getByLabel('Diff, spec excerpt or bug summary').fill('diff --git a/kyc/selfie.py b/kyc/selfie.py\n+ reject printed photos\n')
    await panel.getByRole('button', { name: 'Suggest charters' }).click()
    await expect(suggestions).toContainText('Suggestions for Pasted: diff of kyc/selfie.py')
    await panel.getByRole('tab', { name: 'Imported before' }).click()
    await panel.getByLabel('Artifact').selectOption({ label: 'Diff: PR #42: Check passports against the MRZ (mock PR 42)' })
    await panel.getByRole('button', { name: 'Suggest charters' }).click()
    await expect(suggestions).toContainText('Suggestions for PR #42')
  })

  await test.step('a charter starts a session as the default tester', async () => {
    await yours.getByRole('listitem').first().click()
    await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/sessions/[^/]+$`))
  })
})
