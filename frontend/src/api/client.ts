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
      // Validation errors come as a list: "app_url: Value error, must start with http:// or https://"
      else if (Array.isArray(parsed.detail)) {
        detail = parsed.detail
          .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).filter((l) => l !== 'body').join('.')}: ${d.msg ?? 'invalid'}`)
          .join('; ')
      }
    } catch {
      // not JSON; keep the raw body
    }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return res.json()
}

/** A named address of the app under test, e.g. staging. */
export interface Environment {
  name: string
  url: string
}

/** What a tester works on. Risks, charters, sessions and bugs all belong to one. */
export interface Project {
  id: string
  name: string
  org_id: string
  description: string | null
  default_ai_provider: string
  testing_mission: string | null
  /** Where this project's bugs are filed; credentials live in backend/.env. */
  github_repo: string | null
  jira_project_key: string | null
  /** Default address for the recorded browser, reproduction agent and regression runs. */
  app_url: string | null
  environments: Environment[]
  default_tester: string | null
  /** Set while archived: hidden from the Projects page, still usable. */
  archived_at: string | null
  /** Provider per AI task, e.g. { debrief: 'local' }; other tasks use default_ai_provider. */
  ai_task_providers: Record<string, string>
  created_at: string
}

/** A project on the Projects page. */
export interface ProjectSummary extends Project {
  risk_count: number
  charter_count: number
  session_count: number
  bug_count: number
  last_activity: string | null
}

export type ProjectChanges = Partial<Pick<Project,
  'name' | 'description' | 'testing_mission' | 'default_ai_provider' | 'github_repo' | 'jira_project_key' |
  'app_url' | 'environments' | 'default_tester' | 'ai_task_providers'>>

/** Every address a project knows, default first, without repeats. */
export function projectUrls(project: Project): Environment[] {
  const all = [...(project.app_url ? [{ name: 'default', url: project.app_url }] : []), ...project.environments]
  return all.filter((e, i) => all.findIndex((o) => o.url === e.url) === i)
}

export const AI_PROVIDERS = ['mock', 'claude', 'openai', 'local'] as const

export interface Charter {
  id: string
  project_id: string
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
  project_id: string | null
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
  issue_closed: boolean | null // as seen by the last "Check closed issues"; null: never checked
  issue_status: string | null
  status_checked_at: string | null
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

export interface PlanStep { type: 'goto' | 'action'; url?: string; why?: string; action?: { summary: string } }

/** A recorded-style action, the same shape the recorder produces. */
export interface StepAction {
  action: 'click' | 'fill' | 'select' | 'check' | 'uncheck' | 'press' | 'upload'
  locator: { method: 'label' | 'role' | 'text' | 'placeholder' | 'testid' | 'css'; role?: string; name?: string; value?: string }
  value?: string
  masked?: boolean
  secret?: string
  key?: string
  summary?: string
}

/** A step to the bug: open a page, or one action. */
export interface BugStep { type: 'goto' | 'action'; url?: string | null; action?: StepAction | null; summary?: string | null }

export interface BugSteps {
  steps: BugStep[]
  /** edited: by the tester, used as they are · recording: picked by Mirmera up to the failed request ·
   *  ai: Mirmera can't tell which actions lead to the bug, so the AI picks from these */
  source: 'edited' | 'recording' | 'ai'
  note: string
  anchor: string | null
  recorded_actions: number
}
export interface PlanAssertion { kind: string; why: string; expected?: string; url_contains?: string }

export interface ReplayResult {
  /** reproduces: the test catches the bug. passes: it did not show. broken: a step failed. incomplete: a step was skipped.
   *  misses_bug: the steps ran but never sent the request the bug was recorded on. */
  outcome: 'reproduces' | 'passes' | 'broken' | 'incomplete' | 'misses_bug'
  reach?: { url_contains: string; recorded: string | null; reached: boolean }
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
  project_id: string
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
  project_id: string
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
  project_id: string
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

// ---- Isolate the bug ----------------------------------------------------------

export interface IsolationSymptom { kind: 'failed_request' | 'text'; value: string }
export type VariantResult = 'bug' | 'gone' | 'inconclusive' | 'error'
export interface IsolationRun {
  id: string
  bug_id: string
  status: 'running' | 'done' | 'not_reproduced' | 'cancelled' | 'error'
  base_url: string
  symptom: IsolationSymptom
  baseline: { result: VariantResult; detail: string } | null
  variants: { n: number; step: number; field: string; change: string; result: VariantResult; detail: string }[]
  total: number
  summary: { depends_on: { field: string; gone_with: string[] }[]; irrelevant: string[]; inconclusive: string[] } | null
  detail: string | null
  created_at: string
  finished_at: string | null
}

/** What a delete removed, e.g. { sessions: 2, bugs: 3, evidence: 140 }. */
export interface Deleted { deleted: Record<string, number> }

/** "2 sessions, 3 bugs, 140 evidence items" from a delete's counts. */
export function deletedText(d: Deleted): string {
  const names: Record<string, [string, string]> = {
    sessions: ['session', 'sessions'], bugs: ['bug', 'bugs'], notes: ['note', 'notes'], charters: ['charter', 'charters'],
    risks: ['risk', 'risks'], evidence: ['evidence item', 'evidence items'], regression_tests: ['regression test', 'regression tests'],
    debriefs: ['debrief', 'debriefs'], artifacts: ['artifact', 'artifacts'],
  }
  const parts = Object.entries(names).filter(([k]) => d.deleted[k]).map(([k, [one, many]]) => `${d.deleted[k]} ${d.deleted[k] === 1 ? one : many}`)
  return parts.join(', ') || 'nothing else'
}

// ---- AI tasks and model check ------------------------------------------------

export interface AIOverview {
  tasks: { key: string; label: string; hint: string }[]
  providers: { name: string; configured: boolean; model: string | null; problem: string | null }[]
}
export interface AICheckResult {
  provider: string
  model: string | null
  task: string
  result: 'ok' | 'retried' | 'failed'
  seconds: number
  sample: string | null
  detail: string | null
}

// ---- Regression suite ---------------------------------------------------------

export interface SuiteTest {
  bug_id: string
  bug_title: string
  test_id: string
  filename: string
  known_failing: boolean
  base_url: string | null
  outcome: ReplayResult['outcome'] | null
  meaning: string | null
  replayed_at: string | null
  created_at: string
}
export interface SuiteRun {
  id: string
  project_id: string
  status: 'running' | 'done' | 'cancelled' | 'error'
  base_url: string | null
  total: number
  results: { bug_id: string; bug_title: string; test_id: string; known_failing: boolean
    outcome: ReplayResult['outcome'] | 'error'; meaning: string; duration_ms: number; detail: string }[]
  detail: string | null
  created_at: string
  finished_at: string | null
}
export interface Retest {
  id: string
  project_id: string
  bug_id: string
  bug_title: string
  filing_id: string
  tracker: TrackerName
  issue_key: string
  issue_url: string
  test_id: string | null
  outcome: 'running' | 'fixed' | 'still_fails' | 'could_not_check' | 'no_test'
  replay: ReplayResult | null
  detail: string | null
  commented: boolean
  comment_error: string | null
  created_at: string
  finished_at: string | null
}
export interface RetestCheck { checked: number; filed: number; retests: Retest[]; problems: string[] }
export const suiteExportUrl = (projectId: string) => `${BASE_URL}/projects/${projectId}/regression-tests/export`

export const api = {
  listRegressionTests: (projectId: string) => request<SuiteTest[]>(`/projects/${projectId}/regression-tests`),
  runSuite: (projectId: string, base_url?: string) =>
    request<SuiteRun>(`/projects/${projectId}/regression-suite`, { method: 'POST', body: JSON.stringify({ base_url }) }),
  getSuiteRun: (runId: string) => request<SuiteRun>(`/suite-runs/${runId}`),
  cancelSuiteRun: (runId: string) => request<SuiteRun>(`/suite-runs/${runId}/cancel`, { method: 'POST' }),
  suiteHistory: (projectId: string) => request<SuiteRun[]>(`/projects/${projectId}/suite-runs`),
  checkClosedIssues: (projectId: string, base_url?: string) =>
    request<RetestCheck>(`/projects/${projectId}/retests/check`, { method: 'POST', body: JSON.stringify({ base_url }) }),
  listRetests: (projectId: string) => request<Retest[]>(`/projects/${projectId}/retests`),
  aiOverview: () => request<AIOverview>('/ai'),
  aiCheck: (provider: string, task: string) =>
    request<AICheckResult>('/ai/check', { method: 'POST', body: JSON.stringify({ provider, task }) }),
  listProjects: () => request<ProjectSummary[]>('/projects'),
  archiveProject: (projectId: string) => request<Project>(`/projects/${projectId}/archive`, { method: 'POST' }),
  unarchiveProject: (projectId: string) => request<Project>(`/projects/${projectId}/unarchive`, { method: 'POST' }),
  deleteProject: (projectId: string, confirm: string) =>
    request<Deleted>(`/projects/${projectId}?confirm=${encodeURIComponent(confirm)}`, { method: 'DELETE' }),
  deleteSession: (sessionId: string) => request<Deleted>(`/sessions/${sessionId}`, { method: 'DELETE' }),
  deleteBug: (bugId: string) => request<Deleted>(`/bugs/${bugId}`, { method: 'DELETE' }),
  getProject: (projectId: string) => request<Project>(`/projects/${projectId}`),
  createProject: (project: { name: string; description?: string; app_url?: string }) =>
    request<Project>('/projects', { method: 'POST', body: JSON.stringify(project) }),

  listCharters: (projectId: string) => request<Charter[]>(`/projects/${projectId}/charters`),
  suggestCharters: (projectId: string, artifactIds: string[], provider?: string) =>
    request<CharterSuggestion[]>(`/projects/${projectId}/charters/suggest`, {
      method: 'POST',
      body: JSON.stringify({ artifact_ids: artifactIds, provider }),
    }),
  saveCharter: (projectId: string, charter: CharterInput) =>
    request<Charter>(`/projects/${projectId}/charters`, { method: 'POST', body: JSON.stringify(charter) }),

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
  startReproduction: (bugId: string, opts: { base_url?: string; max_steps: number; runs: number; replay_known_steps?: boolean }) =>
    request<ReproductionRun>(`/bugs/${bugId}/reproduce`, { method: 'POST', body: JSON.stringify(opts) }),
  cancelReproduction: (runId: string) =>
    request<ReproductionRun>(`/reproduction-runs/${runId}/cancel`, { method: 'POST' }),
  regressionFromReproduction: (runId: string) =>
    request<RegressionTest>(`/reproduction-runs/${runId}/regression-test`, { method: 'POST', body: JSON.stringify({}) }),
  latestIsolation: (bugId: string) => request<IsolationRun | null>(`/bugs/${bugId}/isolation`),
  getIsolation: (runId: string) => request<IsolationRun>(`/isolation-runs/${runId}`),
  startIsolation: (bugId: string, opts: { base_url?: string; symptom?: IsolationSymptom }) =>
    request<IsolationRun>(`/bugs/${bugId}/isolation`, { method: 'POST', body: JSON.stringify(opts) }),
  cancelIsolation: (runId: string) => request<IsolationRun>(`/isolation-runs/${runId}/cancel`, { method: 'POST' }),
  getBugSteps: (bugId: string) => request<BugSteps>(`/bugs/${bugId}/steps`),
  saveBugSteps: (bugId: string, steps: BugStep[]) =>
    request<BugSteps>(`/bugs/${bugId}/steps`, { method: 'PUT', body: JSON.stringify({ steps }) }),
  resetBugSteps: (bugId: string) => request<BugSteps>(`/bugs/${bugId}/steps`, { method: 'DELETE' }),
  getRegressionTest: (bugId: string) => request<RegressionTest | null>(`/bugs/${bugId}/regression-test`),
  generateRegressionTest: (bugId: string, knownFailing: boolean) =>
    request<RegressionTest>(`/bugs/${bugId}/regression-test`, { method: 'POST', body: JSON.stringify({ known_failing: knownFailing }) }),
  replayRegressionTest: (testId: string, baseUrl?: string) =>
    request<RegressionTest>(`/regression-tests/${testId}/replay`, { method: 'POST', body: JSON.stringify({ base_url: baseUrl || null }) }),
  trackerStatus: (projectId: string) => request<TrackerStatus[]>(`/projects/${projectId}/trackers`),
  checkDuplicates: (bugId: string, tracker: TrackerName) =>
    request<DuplicateCheck>(`/bugs/${bugId}/duplicates`, { method: 'POST', body: JSON.stringify({ tracker }) }),
  filingPreview: (bugId: string, tracker: TrackerName) =>
    request<FilingPreview>(`/bugs/${bugId}/filing-preview?tracker=${tracker}`),
  fileBug: (bugId: string, req: FileRequest) =>
    request<{ filing: BugFiling; warnings: string[] }>(`/bugs/${bugId}/file`, { method: 'POST', body: JSON.stringify(req) }),
  listAllBugs: (projectId: string) => request<BugListItem[]>(`/projects/${projectId}/bugs`),
  getDebrief: (sessionId: string) => request<Debrief>(`/sessions/${sessionId}/debrief`),
  listDebriefs: (projectId: string) => request<DebriefListItem[]>(`/projects/${projectId}/debriefs`),
  updateDebrief: (sessionId: string, changes: DebriefUpdate) =>
    request<Debrief>(`/sessions/${sessionId}/debrief`, { method: 'PATCH', body: JSON.stringify(changes) }),
  saveOutlookCharter: (sessionId: string, index: number) =>
    request<Debrief>(`/sessions/${sessionId}/debrief/outlook/${index}/charter`, { method: 'POST' }),
  promoteObservedRisk: (sessionId: string, index: number, promotion: ObservedRiskPromotion) =>
    request<Debrief>(`/sessions/${sessionId}/debrief/risks-observed/${index}/risk`, { method: 'POST', body: JSON.stringify(promotion) }),

  updateProject: (projectId: string, changes: ProjectChanges) =>
    request<Project>(`/projects/${projectId}`, { method: 'PATCH', body: JSON.stringify(changes) }),

  listArtifacts: (projectId: string) => request<Artifact[]>(`/projects/${projectId}/artifacts`),
  createArtifact: (projectId: string, artifact: { type: Artifact['type']; label: string; raw_content: string }) =>
    request<Artifact>(`/projects/${projectId}/artifacts`, { method: 'POST', body: JSON.stringify(artifact) }),

  // Resolves to null when no product model has been built yet.
  getProductModel: (projectId: string) =>
    request<ProductModel>(`/projects/${projectId}/product-model`).catch((e: Error) => {
      if (e.message.startsWith('No product model yet')) return null
      throw e
    }),
  buildProductModel: (projectId: string, artifactIds: string[]) =>
    request<ProductModel>(`/projects/${projectId}/product-model`, {
      method: 'POST', body: JSON.stringify({ artifact_ids: artifactIds }),
    }),

  listRisks: (projectId: string, status?: Risk['status']) =>
    request<Risk[]>(`/projects/${projectId}/risks${status ? `?status=${status}` : ''}`),
  analyzeRisks: (projectId: string, maxRisks = 12) =>
    request<Risk[]>(`/projects/${projectId}/risks/analyze`, { method: 'POST', body: JSON.stringify({ max_risks: maxRisks }) }),
  createRisk: (projectId: string, risk: RiskInput) =>
    request<Risk>(`/projects/${projectId}/risks`, { method: 'POST', body: JSON.stringify(risk) }),
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

  chartersFromRisks: (projectId: string, riskIds?: string[]) =>
    request<ChartersFromRisks>(`/projects/${projectId}/charters/from-risks`, {
      method: 'POST', body: JSON.stringify({ risk_ids: riskIds }),
    }),
}
