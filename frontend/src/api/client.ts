// Where the backend runs. Override with VITE_API_URL (the browser tests use their own port).
export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const BASE_URL = API_URL

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  })
  if (!res.ok) {
    // FastAPI puts a human-readable reason in "detail" (e.g. "Set ANTHROPIC_API_KEY...").
    const body = await res.text()
    let detail = body
    try {
      const parsed = JSON.parse(body)
      if (typeof parsed.detail === 'string') detail = parsed.detail
    } catch {
      // not JSON; keep the raw body
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return res.json()
}

export interface Product {
  id: string
  name: string
  org_id: string
  default_ai_provider: string
  testing_mission: string | null
  created_at: string
}

export const AI_PROVIDERS = ['mock', 'claude', 'openai', 'local'] as const

export interface Charter {
  id: string
  product_id: string
  mission_statement: string
  rationale: string | null
  risk_areas: string[]
  priority: 'high' | 'medium' | 'low'
  source: 'ai-suggested' | 'manual' | 'follow-up'
  status: 'proposed' | 'active' | 'done'
  created_at: string
  risk_ids: string[]
  test_ideas: string[]
  oracles: string[]
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
  // The bug report; empty until reviewed. kind null = unreviewed.
  kind: 'bug' | 'question' | null
  steps_to_reproduce: string[]
  expected: string | null
  actual: string | null
  oracle_heuristic: OracleHeuristic | null
  oracle_explanation: string | null
  severity_rationale: string | null
  reproducibility: Reproducibility | null
  open_questions: string[]
  ai_draft: BugDraft | null
  ai_provider: string | null
  ai_model: string | null
  prompt_version: string | null
  last_change: string | null
  reviewed_at: string | null
  /** Screenshot the browser runner took when the bug was logged (empty without the runner). */
  evidence: Evidence[]
}

// ---- Browser session runner ------------------------------------------------

export type EvidenceKind =
  | 'screenshot' | 'trace' | 'har' | 'console_error' | 'page_error' | 'failed_request' | 'navigation'

/** Something the runner recorded. Files are fetched with evidenceFileUrl(). */
export interface Evidence {
  id: string
  session_id: string
  bug_id: string | null
  kind: EvidenceKind
  summary: string
  url: string | null
  has_file: boolean
  /** How many times this same signal was seen. */
  count: number
  captured_at: string
  last_seen_at: string | null
}

export interface RunnerStatus {
  running: boolean
  start_url: string | null
  url: string | null
  title: string | null
  window_open: boolean
  started_at: string | null
  counts: Partial<Record<EvidenceKind, number>>
  dropped: number
}

export const evidenceFileUrl = (evidenceId: string) => `${BASE_URL}/evidence/${evidenceId}/file`

export type Reproducibility = 'always' | 'intermittent' | 'once' | 'unknown'

// FEW HICCUPPS consistency oracles: what makes an observation a problem.
export const ORACLES = {
  claims: 'Inconsistent with specs, docs or other claims',
  user_expectations: 'Inconsistent with what users reasonably expect',
  product: 'Inconsistent with the product elsewhere',
  purpose: "Inconsistent with the product's purpose",
  history: 'Inconsistent with earlier versions',
  comparable_products: 'Inconsistent with comparable products',
  image: "Inconsistent with the company's image",
  statutes: 'Inconsistent with laws, regulations or standards',
  world: 'Inconsistent with how the world works',
  familiarity: 'Matches a familiar problem pattern',
  explainability: "Behaviour that can't be explained",
} as const
export type OracleHeuristic = keyof typeof ORACLES

export interface BugDraft {
  kind: 'bug' | 'question'
  title: string
  summary: string
  steps_to_reproduce: string[]
  expected: string
  actual: string
  oracle: { heuristic: OracleHeuristic; explanation: string } | null
  severity: 'high' | 'medium' | 'low'
  severity_rationale: string
  reproducibility: Reproducibility
  open_questions: string[]
}

export type BugReview = Partial<Pick<Bug,
  'title' | 'description' | 'steps_to_reproduce' | 'expected' | 'actual' | 'oracle_heuristic' |
  'oracle_explanation' | 'severity' | 'severity_rationale' | 'reproducibility' | 'open_questions' | 'status'
>> & { kind?: 'bug' | 'question' }

export type CharterStatus = 'fulfilled' | 'partially_fulfilled' | 'not_fulfilled'

/** A follow-up charter the debrief suggests; charter_id is set once the tester saves it. */
export interface OutlookItem { charter: string; reason: string; charter_id: string | null }
/** A risk noticed during the session; risk_id is set once the tester adds it to the risk list. */
export interface ObservedRisk { text: string; risk_id: string | null }

/** PROOF debrief: Past (final_text / ai_draft), Results, Obstacles, Outlook, Feelings. */
export interface Debrief {
  id: string
  session_id: string
  coverage_summary: string | null
  /** Fractions of session time per phase (0–1): on_charter, opportunity, bug_investigation, setup. */
  time_breakdown: Record<string, number>
  open_questions: string[]
  ai_draft: string | null
  final_text: string | null
  charter_status: CharterStatus | null
  charter_status_rationale: string | null
  results: string[]
  obstacles: string[]
  outlook: OutlookItem[]
  risks_observed: ObservedRisk[]
  /** Written by the tester only; the AI never drafts it. */
  tester_feelings: string | null
  ai_provider: string | null
  ai_model: string | null
  prompt_version: string | null
}

export type DebriefUpdate = Partial<Pick<Debrief, 'final_text' | 'tester_feelings' | 'results' | 'obstacles' | 'open_questions'>>

export interface ObservedRiskPromotion {
  title?: string
  quality_criterion: string
  impact: number
  likelihood: number
}

export interface DebriefListItem {
  id: string
  session_id: string
  charter_mission: string
  ended_at: string | null
  coverage_summary: string | null
  created_at: string
}

export interface BugListItem extends Bug {
  charter_mission: string
}

// ---- Risk model -------------------------------------------------------------

export interface Artifact {
  id: string
  product_id: string
  type: 'spec' | 'diff' | 'bug_import'
  label: string
  ai_summary: string | null
  created_at: string
}

export interface ModelElement {
  category: 'structure' | 'function' | 'data' | 'interfaces' | 'platform' | 'operations' | 'time'
  name: string
  description: string
  sources: string[]
}

export interface ProductModel {
  id: string
  product_id: string
  summary: string
  elements: ModelElement[]
  unknowns: string[]
  artifact_ids: string[]
  ai_provider: string | null
  ai_model: string | null
  prompt_version: string | null
  created_at: string
  truncated_artifacts: string[]
}

export const QUALITY_CRITERIA = [
  'capability', 'reliability', 'usability', 'charisma', 'security',
  'scalability', 'compatibility', 'performance', 'installability', 'development',
] as const

export interface Risk {
  id: string
  product_id: string
  ref: string
  title: string
  failure_mode: string
  quality_criterion: string
  elements: string[]
  impact: number
  likelihood: number
  score: number
  rationale: string | null
  evidence: 'stated' | 'inferred' | 'speculative' | 'tester'
  status: 'proposed' | 'accepted' | 'retired'
  source: 'ai' | 'manual'
  tester_notes: string | null
  ai_provider: string | null
  ai_model: string | null
  prompt_version: string | null
  last_change: string | null
  created_at: string
  updated_at: string
}

export interface RiskInput {
  title: string
  failure_mode: string
  quality_criterion: string
  impact: number
  likelihood: number
  tester_notes?: string
}

export type RiskUpdate = Partial<Pick<Risk, 'title' | 'failure_mode' | 'quality_criterion' | 'impact' | 'likelihood' | 'status' | 'tester_notes'>>

export interface RiskCharterDraft {
  mission_statement: string
  rationale: string
  priority: 'high' | 'medium' | 'low'
  risk_ids: string[]
  risk_refs: string[]
  test_ideas: string[]
  oracles: string[]
}

export interface ChartersFromRisks {
  charters: RiskCharterDraft[]
  uncovered_risk_refs: string[]
  warnings: string[]
}

export interface CharterInput {
  mission_statement: string
  rationale?: string | null
  risk_areas?: string[]
  priority?: 'high' | 'medium' | 'low'
  source?: 'ai-suggested' | 'risk-based' | 'manual'
  risk_ids?: string[]
  test_ideas?: string[]
  oracles?: string[]
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
  saveCharter: (productId: string, charter: CharterInput) =>
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
  startRunner: (sessionId: string, url: string) =>
    request<RunnerStatus>(`/sessions/${sessionId}/runner`, { method: 'POST', body: JSON.stringify({ url }) }),
  runnerStatus: (sessionId: string) => request<RunnerStatus>(`/sessions/${sessionId}/runner`),
  takeScreenshot: (sessionId: string, caption?: string) =>
    request<Evidence>(`/sessions/${sessionId}/runner/screenshot`, { method: 'POST', body: JSON.stringify({ caption }) }),
  stopRunner: (sessionId: string) =>
    request<RunnerStatus>(`/sessions/${sessionId}/runner/stop`, { method: 'POST' }),
  listEvidence: (sessionId: string, kinds?: EvidenceKind[]) =>
    request<Evidence[]>(`/sessions/${sessionId}/evidence${kinds ? `?kind=${kinds.join(',')}` : ''}`),
  bugEvidence: (bugId: string) => request<Evidence[]>(`/bugs/${bugId}/evidence`),
  listAllBugs: (productId: string) => request<BugListItem[]>(`/products/${productId}/bugs`),
  getDebrief: (sessionId: string) => request<Debrief>(`/sessions/${sessionId}/debrief`),
  listDebriefs: (productId: string) => request<DebriefListItem[]>(`/products/${productId}/debriefs`),
  updateDebrief: (sessionId: string, changes: DebriefUpdate) =>
    request<Debrief>(`/sessions/${sessionId}/debrief`, { method: 'PATCH', body: JSON.stringify(changes) }),
  saveOutlookCharter: (sessionId: string, index: number) =>
    request<Debrief>(`/sessions/${sessionId}/debrief/outlook/${index}/charter`, { method: 'POST' }),
  promoteObservedRisk: (sessionId: string, index: number, promotion: ObservedRiskPromotion) =>
    request<Debrief>(`/sessions/${sessionId}/debrief/risks-observed/${index}/risk`, { method: 'POST', body: JSON.stringify(promotion) }),

  updateProduct: (productId: string, changes: Partial<Pick<Product, 'name' | 'testing_mission' | 'default_ai_provider'>>) =>
    request<Product>(`/products/${productId}`, { method: 'PATCH', body: JSON.stringify(changes) }),

  listArtifacts: (productId: string) => request<Artifact[]>(`/products/${productId}/artifacts`),
  createArtifact: (productId: string, artifact: { type: Artifact['type']; label: string; raw_content: string }) =>
    request<Artifact>(`/products/${productId}/artifacts`, { method: 'POST', body: JSON.stringify(artifact) }),

  // Resolves to null when no product model has been built yet.
  getProductModel: (productId: string) =>
    request<ProductModel>(`/products/${productId}/product-model`).catch((e: Error) => {
      if (e.message.startsWith('No product model yet')) return null
      throw e
    }),
  buildProductModel: (productId: string, artifactIds: string[]) =>
    request<ProductModel>(`/products/${productId}/product-model`, {
      method: 'POST', body: JSON.stringify({ artifact_ids: artifactIds }),
    }),

  listRisks: (productId: string, status?: Risk['status']) =>
    request<Risk[]>(`/products/${productId}/risks${status ? `?status=${status}` : ''}`),
  analyzeRisks: (productId: string, maxRisks = 12) =>
    request<Risk[]>(`/products/${productId}/risks/analyze`, { method: 'POST', body: JSON.stringify({ max_risks: maxRisks }) }),
  createRisk: (productId: string, risk: RiskInput) =>
    request<Risk>(`/products/${productId}/risks`, { method: 'POST', body: JSON.stringify(risk) }),
  updateRisk: (riskId: string, changes: RiskUpdate) =>
    request<Risk>(`/risks/${riskId}`, { method: 'PATCH', body: JSON.stringify(changes) }),

  draftBugReport: (bugId: string) =>
    request<Bug>(`/bugs/${bugId}/draft`, { method: 'POST', body: JSON.stringify({}) }),
  reviewBug: (bugId: string, review: BugReview) =>
    request<Bug>(`/bugs/${bugId}`, { method: 'PATCH', body: JSON.stringify(review) }),

  chartersFromRisks: (productId: string, riskIds?: string[]) =>
    request<ChartersFromRisks>(`/products/${productId}/charters/from-risks`, {
      method: 'POST', body: JSON.stringify({ risk_ids: riskIds }),
    }),
}
