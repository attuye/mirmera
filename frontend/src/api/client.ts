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
  /** Where this product's bugs are filed; credentials live in backend/.env. */
  github_repo: string | null
  jira_project_key: string | null
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
  /** Where it was filed: GitHub and/or Jira. */
  filings: BugFiling[]
}

// ---- Issue trackers ----------------------------------------------------------

export type TrackerName = 'github' | 'jira'

export interface TrackerStatus {
  name: TrackerName
  configured: boolean
  target: string | null
  /** What still has to be set, e.g. "GITHUB_TOKEN in backend/.env". */
  missing: string[]
  supports_attachments: boolean
  is_mock: boolean
}

export interface DuplicateCandidate {
  key: string
  title: string
  url: string
  status: string
  closed: boolean
  snippet: string
  verdict: 'likely_duplicate' | 'related' | 'different' | 'not_assessed'
  reason: string
}

export interface DuplicateCheck {
  tracker: TrackerName
  target: string
  searched_for: string[]
  candidates: DuplicateCandidate[]
  ai_provider: string | null
  ai_model: string | null
}

export interface FilingPreview {
  tracker: TrackerName
  title: string
  markdown: string
  attachments: string[]
  attachments_note: string | null
}

export interface BugFiling {
  id: string
  bug_id: string
  tracker: TrackerName
  target: string
  mode: 'new' | 'comment'
  issue_key: string
  issue_url: string
  attachments: string[]
  created_at: string
}

export interface FileRequest { tracker: TrackerName; mode: 'new' | 'comment'; issue_key?: string; note?: string }

// ---- Browser session runner ------------------------------------------------

export type EvidenceKind =
  | 'screenshot' | 'trace' | 'har' | 'console_error' | 'page_error' | 'failed_request' | 'navigation' | 'action'

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
  /** The Chrome extension is recording into this session. */
  extension_active: boolean
}

// ---- Regression tests ---------------------------------------------------------

export interface PlanStep { type: 'goto' | 'action'; url?: string; why: string; action?: { summary: string } }
export interface PlanAssertion { kind: string; why: string; expected?: string; url_contains?: string }

export interface ReplayResult {
  /** reproduces: the test catches the bug. passes: it did not show. broken: a step failed. incomplete: a step was skipped. */
  outcome: 'reproduces' | 'passes' | 'broken' | 'incomplete'
  base_url: string | null
  steps: { n: number; description: string; status: 'ok' | 'failed' | 'skipped' | 'not_run'; detail: string }[]
  assertions: { n: number; kind: string; why: string; status: 'passed' | 'failed' | 'not_run'; detail: string }[]
  duration_ms: number
}

export interface RegressionTest {
  id: string
  bug_id: string
  filename: string
  code: string
  plan: { title: string; base_url: string | null; steps: PlanStep[]; assertions: PlanAssertion[]; assumptions: string[] }
  base_url: string | null
  known_failing: boolean
  warnings: string[]
  ai_provider: string | null
  ai_model: string | null
  replay: ReplayResult | null
  replayed_at: string | null
  created_at: string
}

// ---- Reproduction agent -------------------------------------------------------

export interface AgentStep {
  n: number
  thought: string
  action: string
  kind: string
  /** ok | failed | refused (outside the app, missing secret) | verified | not verified | stopped */
  status: string
  detail: string
  url: string
}

export interface ReproductionRun {
  id: string
  bug_id: string
  status: 'running' | 'reproduced' | 'not_reproduced' | 'cancelled' | 'error'
  base_url: string
  start_url: string
  max_steps: number
  runs: number
  steps: AgentStep[]
  /** What the agent proved, checked by Mirmera: kind, value, explanation, evidence. */
  symptom: { kind: string; value: string; explanation: string; evidence: string } | null
  detail: string | null
  reproducibility: { runs: number; reproduced: number; results: { run: number; reproduced: boolean; detail: string }[] } | null
  has_screenshot: boolean
  ai_provider: string | null
  ai_model: string | null
  created_at: string
  finished_at: string | null
}

export const reproductionScreenshotUrl = (runId: string) => `${BASE_URL}/reproduction-runs/${runId}/screenshot`

export const regressionDownloadUrl = (testId: string) => `${BASE_URL}/regression-tests/${testId}/download`

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
  /** Recorded-browser signal lines the draft relied on, quoted exactly (absent in older drafts). */
  evidence_used?: string[]
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

/** One entry in a risk's score history: a session's suggestion and the
 *  tester's decision, or a manual re-score. */
export interface RiskChange {
  id: string
  risk_id: string
  risk_ref: string
  risk_title: string
  session_id: string | null
  source: 'ai' | 'tester'
  status: 'proposed' | 'accepted' | 'rejected' | 'no_change'
  from_impact: number
  from_likelihood: number
  to_impact: number
  to_likelihood: number
  final_impact: number | null
  final_likelihood: number | null
  /** The risk's score now; when it differs from "from", the suggestion is stale. */
  current_impact: number
  current_likelihood: number
  reason: string | null
  evidence: string[]
  tester_note: string | null
  ai_provider: string | null
  ai_model: string | null
  prompt_version: string | null
  created_at: string
  decided_at: string | null
}

export interface SessionRiskFeedback {
  changes: RiskChange[]
  risk_count: number
  unreviewed_bugs: number
  warnings: string[]
}

export interface RiskChangeDecision { impact?: number; likelihood?: number; note?: string }

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
  latestReproduction: (bugId: string) => request<ReproductionRun | null>(`/bugs/${bugId}/reproduction`),
  getReproduction: (runId: string) => request<ReproductionRun>(`/reproduction-runs/${runId}`),
  startReproduction: (bugId: string, opts: { base_url?: string; max_steps: number; runs: number }) =>
    request<ReproductionRun>(`/bugs/${bugId}/reproduce`, { method: 'POST', body: JSON.stringify(opts) }),
  cancelReproduction: (runId: string) =>
    request<ReproductionRun>(`/reproduction-runs/${runId}/cancel`, { method: 'POST' }),
  regressionFromReproduction: (runId: string) =>
    request<RegressionTest>(`/reproduction-runs/${runId}/regression-test`, { method: 'POST', body: JSON.stringify({}) }),
  getRegressionTest: (bugId: string) => request<RegressionTest | null>(`/bugs/${bugId}/regression-test`),
  generateRegressionTest: (bugId: string, knownFailing: boolean) =>
    request<RegressionTest>(`/bugs/${bugId}/regression-test`, { method: 'POST', body: JSON.stringify({ known_failing: knownFailing }) }),
  replayRegressionTest: (testId: string, baseUrl?: string) =>
    request<RegressionTest>(`/regression-tests/${testId}/replay`, { method: 'POST', body: JSON.stringify({ base_url: baseUrl || null }) }),
  trackerStatus: (productId: string) => request<TrackerStatus[]>(`/products/${productId}/trackers`),
  checkDuplicates: (bugId: string, tracker: TrackerName) =>
    request<DuplicateCheck>(`/bugs/${bugId}/duplicates`, { method: 'POST', body: JSON.stringify({ tracker }) }),
  filingPreview: (bugId: string, tracker: TrackerName) =>
    request<FilingPreview>(`/bugs/${bugId}/filing-preview?tracker=${tracker}`),
  fileBug: (bugId: string, req: FileRequest) =>
    request<{ filing: BugFiling; warnings: string[] }>(`/bugs/${bugId}/file`, { method: 'POST', body: JSON.stringify(req) }),
  listAllBugs: (productId: string) => request<BugListItem[]>(`/products/${productId}/bugs`),
  getDebrief: (sessionId: string) => request<Debrief>(`/sessions/${sessionId}/debrief`),
  listDebriefs: (productId: string) => request<DebriefListItem[]>(`/products/${productId}/debriefs`),
  updateDebrief: (sessionId: string, changes: DebriefUpdate) =>
    request<Debrief>(`/sessions/${sessionId}/debrief`, { method: 'PATCH', body: JSON.stringify(changes) }),
  saveOutlookCharter: (sessionId: string, index: number) =>
    request<Debrief>(`/sessions/${sessionId}/debrief/outlook/${index}/charter`, { method: 'POST' }),
  promoteObservedRisk: (sessionId: string, index: number, promotion: ObservedRiskPromotion) =>
    request<Debrief>(`/sessions/${sessionId}/debrief/risks-observed/${index}/risk`, { method: 'POST', body: JSON.stringify(promotion) }),

  updateProduct: (productId: string, changes: Partial<Pick<Product, 'name' | 'testing_mission' | 'default_ai_provider' | 'github_repo' | 'jira_project_key'>>) =>
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
  riskHistory: (riskId: string) => request<RiskChange[]>(`/risks/${riskId}/history`),
  getRiskUpdates: (sessionId: string) => request<SessionRiskFeedback>(`/sessions/${sessionId}/risk-updates`),
  suggestRiskUpdates: (sessionId: string) =>
    request<SessionRiskFeedback>(`/sessions/${sessionId}/risk-updates`, { method: 'POST', body: JSON.stringify({}) }),
  acceptRiskChange: (changeId: string, decision: RiskChangeDecision) =>
    request<RiskChange>(`/risk-changes/${changeId}/accept`, { method: 'POST', body: JSON.stringify(decision) }),
  rejectRiskChange: (changeId: string, decision: RiskChangeDecision) =>
    request<RiskChange>(`/risk-changes/${changeId}/reject`, { method: 'POST', body: JSON.stringify(decision) }),
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
