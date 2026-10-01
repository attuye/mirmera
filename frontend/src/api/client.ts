const BASE_URL = 'http://localhost:8000'

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  return res.json()
}

export interface Product {
  id: string
  name: string
  org_id: string
  default_ai_provider: string
  created_at: string
}

export interface Charter {
  id: string
  product_id: string
  mission_statement: string
  rationale: string | null
  risk_areas: string[]
  priority: 'high' | 'medium' | 'low'
  source: 'ai-suggested' | 'manual'
  status: 'proposed' | 'active' | 'done'
  created_at: string
}

export interface CharterSuggestion {
  mission_statement: string
  rationale: string
  risk_areas: string[]
  priority: 'high' | 'medium' | 'low'
}

export interface SessionRecord {
  id: string
  charter_id: string
  tester_id: string
  started_at: string
  ended_at: string | null
  paused_at: string | null
  total_paused_seconds: number
  duration_target_minutes: number
}

export interface Note {
  id: string
  session_id: string
  tag: 'test' | 'bug' | 'issue' | 'question' | 'setup'
  content: string
  timestamp: string
}

export interface Bug {
  id: string
  session_id: string
  charter_id: string
  title: string
  description: string | null
  reproduction_trail: { tag: string; content: string; timestamp: string }[]
  environment: Record<string, string>
  severity: 'high' | 'medium' | 'low'
  status: string
  created_at: string
}

export interface Debrief {
  id: string
  session_id: string
  coverage_summary: string | null
  time_breakdown: Record<string, number>
  open_questions: string[]
  ai_draft: string | null
  final_text: string | null
}

export interface DebriefListItem {
  id: string
  session_id: string
  charter_mission: string
  ended_at: string | null
  coverage_summary: string | null
  created_at: string
}

export interface BugListItem {
  id: string
  session_id: string
  charter_mission: string
  title: string
  severity: 'high' | 'medium' | 'low'
  status: string
  reproduction_trail: { tag: string; content: string; timestamp: string }[]
  created_at: string
}

export const api = {
  listProducts: () => request<Product[]>('/products'),
  createProduct: (name: string, org_id: string) =>
    request<Product>('/products', { method: 'POST', body: JSON.stringify({ name, org_id }) }),

  listCharters: (productId: string) => request<Charter[]>(`/products/${productId}/charters`),
  suggestCharters: (productId: string, artifactIds: string[], provider?: string) =>
    request<CharterSuggestion[]>(`/products/${productId}/charters/suggest`, {
      method: 'POST',
      body: JSON.stringify({ artifact_ids: artifactIds, provider }),
    }),
  saveCharter: (productId: string, charter: Omit<Charter, 'id' | 'product_id' | 'status' | 'created_at'>) =>
    request<Charter>(`/products/${productId}/charters`, { method: 'POST', body: JSON.stringify(charter) }),

  startSession: (charter_id: string, tester_id: string) =>
    request<SessionRecord>('/sessions', { method: 'POST', body: JSON.stringify({ charter_id, tester_id }) }),
  getSession: (sessionId: string) => request<SessionRecord>(`/sessions/${sessionId}`),
  pauseSession: (sessionId: string) => request<SessionRecord>(`/sessions/${sessionId}/pause`, { method: 'POST' }),
  resumeSession: (sessionId: string) => request<SessionRecord>(`/sessions/${sessionId}/resume`, { method: 'POST' }),
  getActiveSession: (charterId: string) => request<SessionRecord>(`/charters/${charterId}/active-session`),
  getCharter: (charterId: string) => request<Charter>(`/charters/${charterId}`),
  endSession: (sessionId: string) => request<Debrief>(`/sessions/${sessionId}/end`, { method: 'POST' }),

  listNotes: (sessionId: string) => request<Note[]>(`/sessions/${sessionId}/notes`),
  addNote: (sessionId: string, tag: Note['tag'], content: string) =>
    request<Note>(`/sessions/${sessionId}/notes`, { method: 'POST', body: JSON.stringify({ tag, content }) }),

  listBugs: (sessionId: string) => request<Bug[]>(`/sessions/${sessionId}/bugs`),
  logBug: (sessionId: string, bug: { title: string; description?: string; severity: Bug['severity'] }) =>
    request<Bug>(`/sessions/${sessionId}/bugs`, { method: 'POST', body: JSON.stringify(bug) }),
  listAllBugs: (productId: string) => request<BugListItem[]>(`/products/${productId}/bugs`),
  getDebrief: (sessionId: string) => request<Debrief>(`/sessions/${sessionId}/debrief`),
  listDebriefs: (productId: string) => request<DebriefListItem[]>(`/products/${productId}/debriefs`),
  updateDebrief: (sessionId: string, final_text: string) =>
    request<Debrief>(`/sessions/${sessionId}/debrief`, { method: 'PATCH', body: JSON.stringify({ final_text }) }),
}
