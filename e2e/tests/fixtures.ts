import { test as base, expect, type APIRequestContext } from '@playwright/test'

export const API_URL = 'http://localhost:8100'
export { expect }

// API calls that are allowed to fail because the page expects them to:
// asking for a product model before one has been built returns 404.
const EXPECTED_FAILURES = [/\/product-model$/, /\/artifacts\/import$/]  // a missing product model; an import that says what to fix

/** Small wrapper for setting up data through the API, so each test only
 *  drives the UI for the part it is actually testing. */
export class Api {
  constructor(private readonly ctx: APIRequestContext) {}

  private async json<T>(response: Awaited<ReturnType<APIRequestContext['get']>>): Promise<T> {
    expect(response.ok(), `${response.url()} -> ${response.status()}: ${await response.text()}`).toBeTruthy()
    return response.json() as Promise<T>
  }

  private workspace?: string

  /** The test account's own workspace ("AddisVerify"); the accounts spec adds another. */
  async workspaceId(): Promise<string> {
    if (!this.workspace) {
      const me = await this.json<{ workspaces: { id: string; name: string }[] }>(await this.ctx.get('/auth/me'))
      this.workspace = (me.workspaces.find((w) => w.name === 'AddisVerify') ?? me.workspaces[0]).id
    }
    return this.workspace
  }

  /** An API token, as Account → Chrome extension makes one. */
  async token(label: string): Promise<string> {
    return (await this.json<{ token: string }>(await this.ctx.post('/auth/tokens', { data: { label } }))).token
  }

  /** The project most specs share; created on a fresh database. */
  async project(): Promise<{ id: string; name: string }> {
    const projects = await this.json<{ id: string; name: string }[]>(await this.ctx.get('/projects'))
    const shared = projects.find((p) => p.name === 'AddisVerify KYC Platform')
    if (shared) return shared
    return this.json(await this.ctx.post('/projects', { data: { name: 'AddisVerify KYC Platform', org_id: await this.workspaceId() } }))
  }

  /** A separate project, for specs whose data must not change the shared one
   *  (risk refs like R1 are numbered per project). */
  async newProject(name: string): Promise<{ id: string; name: string }> {
    return this.json(await this.ctx.post('/projects', { data: { name, org_id: await this.workspaceId() } }))
  }

  async charter(projectId: string, mission: string, riskIds: string[] = []): Promise<{ id: string }> {
    return this.json(await this.ctx.post(`/projects/${projectId}/charters`, { data: { mission_statement: mission, risk_ids: riskIds } }))
  }

  /** A tester-added risk (accepted). Its ref (R1, R2…) depends on the risks already there. */
  async risk(projectId: string, title: string, impact: number, likelihood: number): Promise<{ id: string; ref: string }> {
    return this.json(await this.ctx.post(`/projects/${projectId}/risks`, {
      data: { title, failure_mode: `${title}, so fraud gets through`, quality_criterion: 'security', impact, likelihood },
    }))
  }

  async session(charterId: string): Promise<{ id: string }> {
    return this.json(await this.ctx.post('/sessions', { data: { charter_id: charterId, tester_id: 'e2e' } }))
  }

  async note(sessionId: string, tag: 'test' | 'bug' | 'issue' | 'question' | 'setup', content: string): Promise<{ id: string }> {
    return this.json(await this.ctx.post(`/sessions/${sessionId}/notes`, { data: { tag, content } }))
  }

  async bug(sessionId: string, title: string): Promise<{ id: string }> {
    return this.json(await this.ctx.post(`/sessions/${sessionId}/bugs`, { data: { title } }))
  }

  async risks(projectId: string): Promise<{ id: string; title: string; score: number; status: string; source: string }[]> {
    return this.json(await this.ctx.get(`/projects/${projectId}/risks`))
  }

  async charters(projectId: string): Promise<{ id: string; mission_statement: string; source: string }[]> {
    return this.json(await this.ctx.get(`/projects/${projectId}/charters`))
  }

  /** Saves the tester's review, so the bug can be filed. */
  async reviewBug(bugId: string, review: Record<string, unknown>): Promise<{ id: string }> {
    return this.json(await this.ctx.patch(`/bugs/${bugId}`, { data: review }))
  }

  async sessionBugs(sessionId: string): Promise<{ id: string; title: string }[]> {
    return this.json(await this.ctx.get(`/sessions/${sessionId}/bugs`))
  }

  async notes(sessionId: string): Promise<{ content: string }[]> {
    return this.json(await this.ctx.get(`/sessions/${sessionId}/notes`))
  }
}

export const test = base.extend<{ api: Api; appErrors: string[] }>({
  // Runs for every test: fail on any uncaught page error or unexpected failed API call.
  appErrors: [
    async ({ page }, use) => {
      const errors: string[] = []
      page.on('pageerror', (e) => errors.push(`Uncaught error in the page: ${e.message}`))
      page.on('response', (r) => {
        const url = r.url()
        if (url.startsWith(API_URL) && r.status() >= 400 && !EXPECTED_FAILURES.some((p) => p.test(url))) {
          errors.push(`${r.request().method()} ${url} -> ${r.status()}`)
        }
      })
      await use(errors)
      expect(errors, 'page errors or failed API calls during the test').toEqual([])
    },
    { auto: true },
  ],
  api: async ({ playwright }, use) => {
    const ctx = await playwright.request.newContext({ baseURL: API_URL, storageState: '.e2e-data/signed-in.json' })
    await use(new Api(ctx))
    await ctx.dispose()
  },
})
