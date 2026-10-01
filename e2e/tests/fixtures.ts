import { test as base, expect, type APIRequestContext } from '@playwright/test'

export const API_URL = 'http://localhost:8100'
export { expect }

// API calls that are allowed to fail because the page expects them to:
// asking for a product model before one has been built returns 404.
const EXPECTED_FAILURES = [/\/product-model$/]

/** Small wrapper for setting up data through the API, so each test only
 *  drives the UI for the part it is actually testing. */
export class Api {
  constructor(private readonly ctx: APIRequestContext) {}

  private async json<T>(response: Awaited<ReturnType<APIRequestContext['get']>>): Promise<T> {
    expect(response.ok(), `${response.url()} -> ${response.status()}: ${await response.text()}`).toBeTruthy()
    return response.json() as Promise<T>
  }

  /** The app works on the first product; create it if this is a fresh database. */
  async product(): Promise<{ id: string; name: string }> {
    const products = await this.json<{ id: string; name: string }[]>(await this.ctx.get('/products'))
    if (products.length) return products[0]
    return this.json(await this.ctx.post('/products', { data: { name: 'AddisVerify KYC Platform', org_id: 'e2e' } }))
  }

  async charter(productId: string, mission: string): Promise<{ id: string }> {
    return this.json(await this.ctx.post(`/products/${productId}/charters`, { data: { mission_statement: mission } }))
  }

  async session(charterId: string): Promise<{ id: string }> {
    return this.json(await this.ctx.post('/sessions', { data: { charter_id: charterId, tester_id: 'e2e' } }))
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
    const ctx = await playwright.request.newContext({ baseURL: API_URL })
    await use(new Api(ctx))
    await ctx.dispose()
  },
})
