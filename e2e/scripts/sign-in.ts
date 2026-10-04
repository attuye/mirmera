// Runs once before the browser tests (after the servers start): creates the test
// account on the fresh database and saves its sign-in cookie, so every page and
// API call in the specs is signed in.
import { request, type FullConfig } from '@playwright/test'

export const STATE = '.e2e-data/signed-in.json'
export const E2E_USER = { name: 'Attu', email: 'attu@e2e.test', password: 'correct horse battery', workspace_name: 'AddisVerify' }

export default async function signIn(_: FullConfig) {
  const ctx = await request.newContext({ baseURL: 'http://localhost:8100' })
  let r = await ctx.post('/auth/signup', { data: E2E_USER })
  if (r.status() === 409) r = await ctx.post('/auth/login', { data: { email: E2E_USER.email, password: E2E_USER.password } })
  if (!r.ok()) throw new Error(`Could not sign in the test user: ${r.status()} ${await r.text()}`)
  await ctx.storageState({ path: STATE })
  await ctx.dispose()
}
