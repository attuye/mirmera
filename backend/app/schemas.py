"""Pydantic schemas — request/response shapes, and the AI provider's structured output contracts."""
import logging
import re
from datetime import date, datetime
from typing import Any, ClassVar, Optional, Literal

from pydantic.json_schema import SkipJsonSchema
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


# ---- Projects / Artifacts -------------------------------------------------

def _http_url(value: Optional[str]) -> Optional[str]:
    """"" clears; anything else must be an http(s) address."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return ""
    if not re.match(r"^https?://[^\s/]+", value):
        raise ValueError("must start with http:// or https://")
    return value


class Environment(BaseModel):
    """A named address of the app under test, e.g. staging."""
    name: str = Field(min_length=1, max_length=40)
    url: str

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = _http_url(v)
        if not v:
            raise ValueError("is required")
        return v

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("is required")
        return v


def _unique_environments(envs: Optional[list[Environment]]) -> Optional[list[Environment]]:
    if envs is None:
        return None
    names = [e.name.lower() for e in envs]
    if len(names) != len(set(names)):
        raise ValueError("environment names must be unique")
    return envs


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    org_id: Optional[str] = None  # the workspace; leave out when you have only one
    description: Optional[str] = Field(default=None, max_length=300)
    app_url: Optional[str] = None

    @field_validator("app_url")
    @classmethod
    def _app_url(cls, v: Optional[str]) -> Optional[str]:
        return _http_url(v) or None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("is required")
        return v


class ProjectOut(BaseModel):
    id: str
    name: str
    org_id: str
    description: Optional[str] = None
    default_ai_provider: str
    testing_mission: Optional[str] = None
    github_repo: Optional[str] = None
    jira_project_key: Optional[str] = None
    app_url: Optional[str] = None
    environments: list[Environment] = []
    default_tester: Optional[str] = None
    ethiopia: bool = False  # users in Ethiopia: phone, name, Birr and address ideas, and the format check
    archived_at: Optional[datetime] = None
    ai_task_providers: dict[str, str] = {}
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("environments", mode="before")
    @classmethod
    def _none_is_empty(cls, v):
        return v or []

    @field_validator("ai_task_providers", mode="before")
    @classmethod
    def _none_is_no_choices(cls, v):
        return v or {}


class ProjectSummary(ProjectOut):
    """A project on the Projects page, with a few counts."""
    risk_count: int = 0
    charter_count: int = 0
    session_count: int = 0
    bug_count: int = 0
    last_activity: Optional[datetime] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=300)
    testing_mission: Optional[str] = None
    default_ai_provider: Optional[str] = None  # validated against the registered providers
    github_repo: Optional[str] = Field(default=None, pattern=r"^$|^[\w.-]+/[\w.-]+$")  # owner/repo; "" clears
    jira_project_key: Optional[str] = Field(default=None, pattern=r"^$|^[A-Z][A-Z0-9_]+$")  # e.g. KYC; "" clears
    app_url: Optional[str] = None  # "" clears
    environments: Optional[list[Environment]] = Field(default=None, max_length=10)
    default_tester: Optional[str] = Field(default=None, max_length=60)  # "" clears
    ethiopia: Optional[bool] = None
    # {"debrief": "local"}: tasks left out use the default provider; "" removes a choice
    ai_task_providers: Optional[dict[str, str]] = None

    @field_validator("app_url")
    @classmethod
    def _app_url(cls, v: Optional[str]) -> Optional[str]:
        return _http_url(v)

    @field_validator("environments")
    @classmethod
    def _envs(cls, v: Optional[list[Environment]]) -> Optional[list[Environment]]:
        return _unique_environments(v)


class ArtifactCreate(BaseModel):
    type: Literal["spec", "diff", "bug_import"]
    label: str
    raw_content: Optional[str] = None
    file_ref: Optional[str] = None


class ArtifactImport(BaseModel):
    """Where to import from. github_pr: ref. github_issues: labels. jira_issues: jql.
    git_diff: repo_path, base, head (a repository on the computer running Mirmera)."""
    source: Literal["github_pr", "github_issues", "jira_issues", "git_diff"]
    ref: str = ""
    labels: str = "bug"
    jql: str = ""
    limit: int = Field(default=50, ge=1, le=100)
    repo_path: str = ""
    base: str = "main"
    head: str = "HEAD"


class ArtifactOut(BaseModel):
    id: str
    project_id: str
    type: str
    label: str
    file_ref: Optional[str] = None  # where an imported artifact came from
    ai_summary: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---- AI provider contracts --------------------------------------------
# Every AIProvider implementation must fill these exact shapes, regardless
# of which model is behind it — see app/ai/base.py.

class CharterSuggestion(BaseModel):
    mission_statement: str
    rationale: str
    risk_areas: list[str]
    priority: Literal["high", "medium", "low"]

class CharterSuggestionList(BaseModel):
    suggestions: list[CharterSuggestion]

    @model_validator(mode="before")
    @classmethod
    def drop_broken_items(cls, data):
        data, dropped = keep_valid(data, "suggestions", CharterSuggestion)
        _log_dropped("charter suggestion", dropped)
        return data

class DebriefDraft(BaseModel):
    coverage_summary: str
    open_questions: list[str]


# ---- Charters ---------------------------------------------------------

class CharterSuggestRequest(BaseModel):
    artifact_ids: list[str]
    provider: Optional[str] = None  # override the project's default for this call


class CharterCreate(BaseModel):
    mission_statement: str
    rationale: Optional[str] = None
    risk_areas: list[str] = []
    priority: Literal["high", "medium", "low"] = "medium"
    source: Literal["ai-suggested", "risk-based", "follow-up", "manual"] = "manual"
    source_artifact_id: Optional[str] = None
    risk_ids: list[str] = []  # risks this charter investigates (must belong to the project)
    test_ideas: list[str] = []
    oracles: list[str] = []
    heuristics: list[str] = []  # keys from the heuristics library
    story_ids: list[str] = []  # stories this charter belongs to (same project)


class StoryRef(BaseModel):
    id: str
    key: Optional[str] = None
    title: str


class CharterOut(BaseModel):
    id: str
    project_id: str
    mission_statement: str
    rationale: Optional[str]
    risk_areas: list[str]
    priority: str
    source: str
    status: str
    created_at: datetime
    risk_ids: list[str] = []
    test_ideas: list[str] = []
    oracles: list[str] = []
    heuristics: list[str] = []
    source_artifact_id: Optional[str] = None
    source_label: Optional[str] = None  # the change it was suggested from
    source_url: Optional[str] = None
    stories: list[StoryRef] = Field(default=[], validation_alias="story_refs")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("risk_areas", "test_ideas", "oracles", "heuristics", mode="before")
    @classmethod
    def none_is_empty(cls, value):
        # Charters saved before these columns existed have NULL here.
        return value or []


# ---- Sessions / Notes / Bugs -------------------------------------------

class SessionCreate(BaseModel):
    charter_id: str
    tester_id: str
    duration_target_minutes: float = 90


class SessionOut(BaseModel):
    id: str
    charter_id: str
    project_id: Optional[str] = None
    tester_id: str
    started_at: datetime
    ended_at: Optional[datetime]
    paused_at: Optional[datetime]
    total_paused_seconds: float
    duration_target_minutes: float

    model_config = ConfigDict(from_attributes=True)


class NoteCreate(BaseModel):
    tag: Literal["test", "bug", "issue", "question", "setup"]
    content: str


class NoteOut(BaseModel):
    id: str
    session_id: str
    tag: str
    content: str
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


class BugCreate(BaseModel):
    title: str
    description: Optional[str] = None
    severity: Literal["high", "medium", "low"] = "medium"
    environment: dict = {}
    attachments: list[str] = []
    # how many preceding notes to snapshot into the reproduction trail
    trail_window: int = 6
    evidence_ids: list[str] = Field(default=[], max_length=20)  # e.g. the API request that showed it


class EvidenceOut(BaseModel):
    """A screenshot, trace, HAR file or signal recorded by the session runner."""
    id: str
    session_id: str
    bug_id: Optional[str] = None
    kind: str
    summary: str
    url: Optional[str] = None
    has_file: bool = False
    count: int = 1
    captured_at: datetime
    last_seen_at: Optional[datetime] = None
    data: Optional[dict] = None  # an API request: method, redacted request, status, timing, response start

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def file_flag(cls, data):
        # The file path stays on the server; the client only needs to know a file exists.
        if hasattr(data, "file_path"):
            return {**{c: getattr(data, c) for c in (
                "id", "session_id", "bug_id", "kind", "summary", "url", "count", "captured_at", "last_seen_at")},
                "has_file": bool(data.file_path),
                "data": data.data if data.kind == "api_call" else None}  # other kinds' details stay on the server
        return data


class BugOut(BaseModel):
    id: str
    session_id: str
    charter_id: str
    title: str
    description: Optional[str]
    reproduction_trail: list
    environment: dict
    severity: str
    status: str
    created_at: datetime
    # The bug report (empty until reviewed)
    kind: Optional[str] = None  # None = unreviewed
    steps_to_reproduce: list[str] = []
    expected: Optional[str] = None
    actual: Optional[str] = None
    oracle_heuristic: Optional[str] = None
    oracle_explanation: Optional[str] = None
    severity_rationale: Optional[str] = None
    reproducibility: Optional[str] = None
    open_questions: list[str] = []
    ai_draft: Optional[dict] = None
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    prompt_version: Optional[str] = None
    last_change: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    # Screenshot(s) the runner took when the bug was logged
    evidence: list[EvidenceOut] = []
    # Where it was filed: GitHub and/or Jira
    filings: list["BugFilingOut"] = []
    # The exploration agent met the same problem again: where and when
    sightings: list[dict] = []
    # Mirmera replayed the draft's steps in fresh browsers: {"runs", "reproduced", "results", "at"}
    confirmation: Optional[dict] = None
    # A tester dismissed it: {"reason", "note", "by", "at"}. Mirmera won't draft the same problem again.
    dismissal: Optional[dict] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("steps_to_reproduce", "open_questions", "reproduction_trail", "sightings", mode="before")
    @classmethod
    def none_is_empty_list(cls, value):
        return value or []

    @field_validator("environment", mode="before")
    @classmethod
    def none_is_empty_dict(cls, value):
        return value or {}


class BugListItem(BugOut):
    charter_mission: str

# ---- Debrief ------------------------------------------------------------

class OutlookItem(BaseModel):
    charter: str
    reason: str
    charter_id: Optional[str] = None  # set once saved as a charter


class ObservedRisk(BaseModel):
    text: str
    risk_id: Optional[str] = None  # set once promoted to a risk


class DebriefOut(BaseModel):
    id: str
    session_id: str
    coverage_summary: Optional[str]
    time_breakdown: dict
    open_questions: list
    ai_draft: Optional[str]
    final_text: Optional[str]
    # PROOF
    charter_status: Optional[str] = None
    charter_status_rationale: Optional[str] = None
    results: list[str] = []
    obstacles: list[str] = []
    outlook: list[OutlookItem] = []
    risks_observed: list[ObservedRisk] = []
    tester_feelings: Optional[str] = None
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    prompt_version: Optional[str] = None
    drafting: bool = False  # the AI is still writing it: show that, and fill in when it's done

    model_config = ConfigDict(from_attributes=True)

    @field_validator("open_questions", "results", "obstacles", "outlook", "risks_observed", mode="before")
    @classmethod
    def none_is_empty(cls, value):
        # Debriefs written before the PROOF step have NULL here.
        return value or []

    @field_validator("time_breakdown", mode="before")
    @classmethod
    def none_is_empty_dict(cls, value):
        return value or {}

    @field_validator("drafting", mode="before")
    @classmethod
    def none_is_not_drafting(cls, value):
        return bool(value)  # debriefs saved before this column existed

class DebriefListItem(BaseModel):
    id: str
    session_id: str
    charter_mission: str
    ended_at: Optional[datetime]
    coverage_summary: Optional[str]
    created_at: datetime
    
class DebriefUpdate(BaseModel):
    """The tester's edits. Only the fields sent are changed."""
    final_text: Optional[str] = None
    tester_feelings: Optional[str] = None
    results: Optional[list[str]] = None
    obstacles: Optional[list[str]] = None
    open_questions: Optional[list[str]] = None


# ---- Risk model ----------------------------------------------------------
# Product model (SFDIPOT) -> risks (tester reviews) -> charters from risks.

SFDIPOT = Literal["structure", "function", "data", "interfaces", "platform", "operations", "time"]
QualityCriterion = Literal[
    "capability", "reliability", "usability", "charisma", "security",
    "scalability", "compatibility", "performance", "installability", "development",
]
OracleHeuristic = Literal[
    "familiarity", "explainability", "world", "history", "image",
    "comparable_products", "claims", "user_expectations", "product", "purpose", "statutes",
]
RiskEvidence = Literal["stated", "inferred", "speculative", "tester"]
RiskStatus = Literal["proposed", "accepted", "retired"]


# AI contracts
#
# Smaller models (the local provider) often get one item of a reply wrong. Where
# a safe fallback exists, the contracts below repair the reply instead of
# rejecting all of it: a broken item in a list of proposals is dropped, a bug
# without an oracle becomes a question, a re-score without evidence becomes
# "unchanged". Nothing is made up. A reply with nothing usable left still fails,
# and the AI is asked again with the error.

def keep_valid(data, field: str, model: type[BaseModel]) -> tuple[dict, list[tuple[object, str]]]:
    """Splits data[field] into items that validate as `model` and (item, reason) for
    those that don't. Returns data unchanged when nothing (or everything) is broken,
    so the normal validation error and retry still happen."""
    if not isinstance(data, dict) or not isinstance(data.get(field), list):
        return data, []
    kept, dropped = [], []
    for raw in data[field]:
        try:
            model.model_validate(raw)
            kept.append(raw)
        except ValidationError as err:
            dropped.append((raw, err.errors()[0]["msg"].removeprefix("Value error, ")))
    if not kept or not dropped:
        return data, []
    return {**data, field: kept}, dropped


def _log_dropped(what: str, dropped: list) -> None:
    for _, reason in dropped:
        logging.getLogger(__name__).warning("AI reply: dropped a %s that was incomplete (%s)", what, reason)


class ModelElement(BaseModel):
    category: SFDIPOT
    name: str
    description: str
    sources: list[str] = []


class LLMProductModel(BaseModel):
    summary: str
    elements: list[ModelElement]
    unknowns: list[str]


class LLMRisk(BaseModel):
    title: str
    failure_mode: str
    quality_criterion: QualityCriterion
    elements: list[str]
    impact: int = Field(ge=1, le=5)
    likelihood: int = Field(ge=1, le=5)
    rationale: str
    evidence: Literal["stated", "inferred", "speculative"]


class LLMRiskList(BaseModel):
    risks: list[LLMRisk]

    @model_validator(mode="before")
    @classmethod
    def drop_broken_items(cls, data):
        data, dropped = keep_valid(data, "risks", LLMRisk)
        _log_dropped("risk", dropped)
        return data


class LLMStoryRiskLink(BaseModel):
    risk_ref: str
    reason: str = Field(min_length=3)


class LLMStoryRisks(BaseModel):
    """A story's risks: the project's risks it touches, and the ones it brings in."""
    touches: list[LLMStoryRiskLink] = []
    new_risks: list[LLMRisk] = []
    thin: Optional[str] = None  # the story says too little: what is missing

    @model_validator(mode="before")
    @classmethod
    def drop_broken_items(cls, data):
        data, dropped = keep_valid(data, "new_risks", LLMRisk)
        _log_dropped("risk", dropped)
        data, dropped = keep_valid(data, "touches", LLMStoryRiskLink)
        _log_dropped("risk link", dropped)
        return data


class LLMRiskCharter(BaseModel):
    target: str
    resources: str
    information: str
    rationale: str
    risk_refs: list[str] = Field(min_length=1)
    test_ideas: list[str]
    oracles: list[OracleHeuristic]
    priority: Literal["high", "medium", "low"]


class LLMRiskCharterList(BaseModel):
    charters: list[LLMRiskCharter]

    @model_validator(mode="before")
    @classmethod
    def drop_broken_items(cls, data):
        data, dropped = keep_valid(data, "charters", LLMRiskCharter)
        _log_dropped("charter", dropped)
        return data


# API shapes

class ProductModelRequest(BaseModel):
    artifact_ids: list[str] = Field(min_length=1)
    provider: Optional[str] = None


class ProductModelOut(BaseModel):
    id: str
    project_id: str
    summary: str
    elements: list[ModelElement]
    unknowns: list[str]
    artifact_ids: list[str]
    ai_provider: Optional[str]
    ai_model: Optional[str]
    prompt_version: Optional[str]
    created_at: datetime
    truncated_artifacts: list[str] = []  # artifacts cut to fit; set on creation only

    model_config = ConfigDict(from_attributes=True)


class RiskAnalyzeRequest(BaseModel):
    provider: Optional[str] = None
    max_risks: int = Field(default=12, ge=3, le=30)


class StoryRisksRequest(BaseModel):
    """Suggest risks for one story: the project's risks it touches, and new ones."""
    story_id: str
    provider: Optional[str] = None
    max_risks: int = Field(default=5, ge=1, le=10)


class StoryChartersRequest(BaseModel):
    """Charters for one story, from its accepted risks."""
    story_id: str
    provider: Optional[str] = None
    max_charters: int = Field(default=4, ge=1, le=10)


class AiJobItemUpdate(BaseModel):
    """A suggestion in a finished job, accepted (with the charter made from it) or dropped."""
    index: int = Field(ge=0)
    status: Literal["accepted", "dropped"]
    charter_id: Optional[str] = None


class CharterUpdate(BaseModel):
    """Edit a charter that no session has used yet."""
    mission_statement: Optional[str] = Field(default=None, min_length=10, max_length=600)
    rationale: Optional[str] = None
    priority: Optional[Literal["high", "medium", "low"]] = None


class StoryRiskDecision(BaseModel):
    action: Literal["link", "drop"]


class RiskCreate(BaseModel):
    """A risk the tester adds by hand; accepted straight away."""
    title: str = Field(min_length=3)
    failure_mode: str = Field(min_length=3)
    quality_criterion: QualityCriterion
    elements: list[str] = []
    impact: int = Field(ge=1, le=5)
    likelihood: int = Field(ge=1, le=5)
    rationale: Optional[str] = None
    tester_notes: Optional[str] = None


class RiskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=3)
    failure_mode: Optional[str] = Field(default=None, min_length=3)
    quality_criterion: Optional[QualityCriterion] = None
    impact: Optional[int] = Field(default=None, ge=1, le=5)
    likelihood: Optional[int] = Field(default=None, ge=1, le=5)
    status: Optional[RiskStatus] = None
    tester_notes: Optional[str] = None


class RiskOut(BaseModel):
    id: str
    project_id: str
    ref: str
    title: str
    failure_mode: str
    quality_criterion: str
    elements: list[str]
    impact: int
    likelihood: int
    score: int
    rationale: Optional[str]
    evidence: str
    status: str
    source: str
    tester_notes: Optional[str]
    ai_provider: Optional[str]
    ai_model: Optional[str]
    prompt_version: Optional[str]
    last_change: Optional[str]
    created_at: datetime
    updated_at: datetime
    stories: list[StoryRef] = Field(default=[], validation_alias="story_refs")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ChartersFromRisksRequest(BaseModel):
    risk_ids: Optional[list[str]] = None  # default: every accepted risk
    provider: Optional[str] = None
    max_charters: int = Field(default=6, ge=1, le=20)


class RiskCharterDraft(BaseModel):
    """A charter proposal; save it with POST /projects/{id}/charters to use it."""
    mission_statement: str
    rationale: str
    priority: Literal["high", "medium", "low"]
    risk_ids: list[str]
    risk_refs: list[str]
    test_ideas: list[str]
    oracles: list[str]


class ChartersFromRisksOut(BaseModel):
    charters: list[RiskCharterDraft]
    uncovered_risk_refs: list[str]  # accepted risks no charter covers
    warnings: list[str]


# ---- Bug reports -----------------------------------------------------------
# Quick-logged bug -> AI draft (kept for comparison) -> tester review as bug or question.

BugSeverity = Literal["high", "medium", "low"]
Reproducibility = Literal["always", "intermittent", "once", "unknown"]


class Oracle(BaseModel):
    heuristic: OracleHeuristic
    explanation: str


class LLMBugDraft(BaseModel):
    """AI contract. A bug must name its oracle; without one it is a question."""
    kind: Literal["bug", "question"]
    title: str
    summary: str
    steps_to_reproduce: list[str]
    expected: str
    actual: str
    oracle: Optional[Oracle] = None
    severity: BugSeverity
    severity_rationale: str
    reproducibility: Reproducibility
    open_questions: list[str] = []
    # Recorded-browser signals the draft relied on, quoted, so the tester can
    # check them. Empty when the browser wasn't used or nothing was relevant.
    evidence_used: list[str] = []

    NO_ORACLE_QUESTION: ClassVar[str] = ("The AI drafted this as a bug but named no oracle. Which expectation "
                                         "does it break (a spec, a comparable product, the product's own behaviour elsewhere)?")

    @model_validator(mode="before")
    @classmethod
    def no_oracle_means_question(cls, data):
        """A bug must name its oracle; without one it is a question. Smaller models
        sometimes call it a bug anyway: keep the draft, as a question that asks for
        the oracle, instead of rejecting it."""
        if isinstance(data, dict) and data.get("kind") == "bug" and not data.get("oracle"):
            questions = data.get("open_questions") if isinstance(data.get("open_questions"), list) else []
            return {**data, "kind": "question", "open_questions": [*questions, cls.NO_ORACLE_QUESTION]}
        return data

    @model_validator(mode="after")
    def bug_needs_oracle(self):
        if self.kind == "bug" and self.oracle is None:
            raise ValueError("A 'bug' must name an oracle; otherwise use kind='question'")
        return self


class BugDraftRequest(BaseModel):
    provider: Optional[str] = None


class BugReview(BaseModel):
    """The tester's version of the report. Only the fields sent are changed."""
    title: Optional[str] = Field(default=None, min_length=3)
    description: Optional[str] = None
    kind: Optional[Literal["bug", "question"]] = None
    steps_to_reproduce: Optional[list[str]] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    oracle_heuristic: Optional[OracleHeuristic] = None
    oracle_explanation: Optional[str] = None
    severity: Optional[BugSeverity] = None
    severity_rationale: Optional[str] = None
    reproducibility: Optional[Reproducibility] = None
    open_questions: Optional[list[str]] = None
    status: Optional[str] = None


# ---- PROOF debrief ----------------------------------------------------------

class FollowUpCharter(BaseModel):
    charter: str
    reason: str


class LLMDebrief(BaseModel):
    """AI contract for the session debrief. Feelings are deliberately absent:
    only the tester can say how the session felt."""
    charter_status: Literal["fulfilled", "partially_fulfilled", "not_fulfilled"]
    charter_status_rationale: str
    past: str
    results: list[str]
    obstacles: list[str]
    outlook: list[FollowUpCharter]
    risks_observed: list[str]
    open_questions: list[str]


class ObservedRiskPromotion(BaseModel):
    """Turn a risk observed in a session into a real (accepted) risk."""
    title: Optional[str] = Field(default=None, min_length=3)
    quality_criterion: QualityCriterion = "capability"
    impact: int = Field(default=3, ge=1, le=5)
    likelihood: int = Field(default=3, ge=1, le=5)


# ---------- browser session runner ----------

class RunnerStart(BaseModel):
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("url")
    @classmethod
    def http_url(cls, value: str) -> str:
        value = value.strip()
        if "://" not in value:
            value = "https://" + value  # "staging.example.com" is what testers type
        if not value.startswith(("http://", "https://")):
            raise ValueError("Use an http:// or https:// address")
        return value


class ScreenshotRequest(BaseModel):
    caption: Optional[str] = Field(default=None, max_length=500)


class RunnerStatus(BaseModel):
    running: bool
    start_url: Optional[str] = None
    url: Optional[str] = None  # page the tester is on now
    title: Optional[str] = None
    window_open: bool = False
    started_at: Optional[datetime] = None
    counts: dict[str, int] = {}  # signals and screenshots recorded so far, by kind
    dropped: int = 0  # signals not recorded because the page produced too many
    # The Chrome extension is recording into this session (it sent something in the last 2 minutes)
    extension_active: bool = False


# ---------- risk feedback: sessions suggest risk score changes ----------

class LLMRiskUpdate(BaseModel):
    """AI contract: a suggested new score for one of the given risks."""
    risk_ref: str
    impact: int = Field(ge=1, le=5)
    likelihood: int = Field(ge=1, le=5)
    reason: str = Field(min_length=3)
    evidence: list[str] = Field(min_length=1)  # quoted lines; a suggestion needs evidence


class LLMRiskUnchanged(BaseModel):
    risk_ref: str
    reason: str  # e.g. not exercised; exercised and held up but one session is not enough


class LLMRiskUpdateList(BaseModel):
    updates: list[LLMRiskUpdate] = []
    unchanged: list[LLMRiskUnchanged] = []

    @model_validator(mode="before")
    @classmethod
    def unsupported_updates_are_unchanged(cls, data):
        """A re-score needs quoted evidence and scores from 1 to 5. One that lacks them
        is not applied: it is listed as unchanged, with the reason, so no score moves
        without evidence and the rest of the reply still counts."""
        if not isinstance(data, dict) or not isinstance(data.get("updates"), list):
            return data
        kept, unchanged = [], list(data.get("unchanged") or []) if isinstance(data.get("unchanged"), list) else []
        for raw in data["updates"]:
            try:
                LLMRiskUpdate.model_validate(raw)
                kept.append(raw)
            except ValidationError as err:
                ref = raw.get("risk_ref") if isinstance(raw, dict) else None
                if not ref:
                    continue  # nothing to attach it to
                problem = err.errors()[0]
                field = ".".join(str(x) for x in problem["loc"]) or "reply"
                unchanged.append({"risk_ref": str(ref), "reason": f"The AI suggested a new score but it was not usable "
                                  f"({field}: {problem['msg']}), so the score stays as it is."})
        return {**data, "updates": kept, "unchanged": unchanged}


class RiskSuggestRequest(BaseModel):
    provider: Optional[str] = None


class RiskChangeDecision(BaseModel):
    """Accept, optionally with the tester's own numbers, or reject with a note."""
    impact: Optional[int] = Field(default=None, ge=1, le=5)
    likelihood: Optional[int] = Field(default=None, ge=1, le=5)
    note: Optional[str] = Field(default=None, max_length=2000)


class RiskChangeOut(BaseModel):
    id: str
    risk_id: str
    risk_ref: str
    risk_title: str
    session_id: Optional[str] = None
    source: str
    status: str
    from_impact: int
    from_likelihood: int
    to_impact: int
    to_likelihood: int
    final_impact: Optional[int] = None
    final_likelihood: Optional[int] = None
    # The risk's score now: if it moved since the suggestion, accepting is refused.
    current_impact: int
    current_likelihood: int
    reason: Optional[str] = None
    evidence: list[str] = []
    tester_note: Optional[str] = None
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    prompt_version: Optional[str] = None
    created_at: datetime
    decided_at: Optional[datetime] = None

    @classmethod
    def of(cls, change) -> "RiskChangeOut":
        risk = change.risk
        return cls(
            **{c: getattr(change, c) for c in (
                "id", "risk_id", "session_id", "source", "status", "from_impact", "from_likelihood",
                "to_impact", "to_likelihood", "final_impact", "final_likelihood", "reason", "tester_note",
                "ai_provider", "ai_model", "prompt_version", "created_at", "decided_at")},
            evidence=change.evidence or [],
            risk_ref=risk.ref, risk_title=risk.title,
            current_impact=risk.impact, current_likelihood=risk.likelihood,
        )


class SessionRiskFeedback(BaseModel):
    changes: list[RiskChangeOut]
    risk_count: int  # risks linked to the session's charter
    unreviewed_bugs: int  # suggestions are better once bugs are reviewed
    warnings: list[str] = []


# ---------- issue trackers ----------

DuplicateVerdict = Literal["likely_duplicate", "related", "different"]


class LLMDuplicate(BaseModel):
    key: str
    verdict: DuplicateVerdict
    reason: str


class LLMDuplicateList(BaseModel):
    """AI contract: one verdict per candidate issue."""
    verdicts: list[LLMDuplicate] = []

    @model_validator(mode="before")
    @classmethod
    def drop_broken_items(cls, data):
        data, dropped = keep_valid(data, "verdicts", LLMDuplicate)
        _log_dropped("duplicate verdict", dropped)
        return data


class TrackerStatus(BaseModel):
    name: str
    configured: bool
    target: Optional[str] = None  # repo or project key
    missing: list[str] = []
    supports_attachments: bool = False
    is_mock: bool = False


class TrackerRequest(BaseModel):
    tracker: Literal["github", "jira"]
    provider: Optional[str] = None  # AI provider for the duplicate check


class DuplicateCandidate(BaseModel):
    key: str
    title: str
    url: str
    status: str
    closed: bool
    snippet: str = ""
    # same_problem: Mirmera knows it's the same problem without AI (its fingerprint, or its own earlier filing)
    verdict: Literal["same_problem", "likely_duplicate", "related", "different", "not_assessed"]
    reason: str


class DuplicateCheckOut(BaseModel):
    tracker: str
    target: str
    searched_for: list[str]  # the words used, so the tester can judge the search
    candidates: list[DuplicateCandidate]
    fingerprint: Optional[str] = None  # the problem's Mirmera fingerprint, written into every issue it files
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    prompt_version: Optional[str] = None


class FilingPreview(BaseModel):
    tracker: str
    title: str
    markdown: str
    attachments: list[str]  # screenshot files that will be attached
    attachments_note: Optional[str] = None  # why some won't be


class FileRequest(BaseModel):
    tracker: Literal["github", "jira"]
    mode: Literal["new", "comment"] = "new"
    issue_key: Optional[str] = Field(default=None, max_length=50)  # required for "comment"
    note: Optional[str] = Field(default=None, max_length=2000)
    # A new issue is refused while the same problem is already filed (same fingerprint), unless the tester says so.
    file_anyway: bool = False

    @model_validator(mode="after")
    def comment_needs_issue(self):
        if self.mode == "comment" and not (self.issue_key or "").strip():
            raise ValueError("Adding to an existing issue needs its key, e.g. #42 or KYC-42")
        return self


class BugFilingOut(BaseModel):
    id: str
    bug_id: str
    tracker: str
    target: str
    mode: str
    issue_key: str
    issue_url: str
    attachments: list[str] = []
    created_at: datetime
    issue_closed: Optional[bool] = None  # as seen by the last "Check closed issues"
    issue_status: Optional[str] = None
    status_checked_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class FileResult(BaseModel):
    filing: BugFilingOut
    warnings: list[str] = []


BugOut.model_rebuild()
BugListItem.model_rebuild()


# ---------- regression tests: AI plan -> rendered Playwright test -> replay ----------

PLAN_ROLES = (
    "alert", "button", "cell", "checkbox", "combobox", "dialog", "heading", "img", "link", "listitem",
    "menuitem", "option", "progressbar", "radio", "row", "searchbox", "spinbutton", "status", "switch",
    "tab", "textbox",
)


class PlanLocator(BaseModel):
    """How the test finds an element; mirrors Playwright's getBy* locators."""
    method: Literal["role", "label", "placeholder", "text", "testid"]  # no CSS: assertions should read like a user
    role: Optional[str] = None
    name: Optional[str] = None
    value: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def forgiving(cls, data):
        """Small models mix up the fields: {"method": "label", "name": "Document number"} or
        {"method": "role", "role": "button", "value": "Verify"} mean what they say, so read them
        that way instead of failing the whole step."""
        if not isinstance(data, dict):
            return data
        d = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}
        if isinstance(d.get("method"), str):
            d["method"] = d["method"].lower()
        if isinstance(d.get("role"), str):
            d["role"] = d["role"].lower()
        if d.get("method") == "role":
            if not d.get("name") and d.get("value"):
                d["name"] = d.pop("value")
        elif d.get("method") in ("label", "placeholder", "text", "testid") and not d.get("value"):
            d["value"] = d.pop("name", None) or (d.get("role") if d.get("role") not in PLAN_ROLES else None)
        return d

    @model_validator(mode="after")
    def complete(self):
        if self.method == "role":
            if self.role not in PLAN_ROLES:
                raise ValueError(f"role must be one of {', '.join(PLAN_ROLES)}")
        elif not (self.value or "").strip():
            raise ValueError(f"a {self.method} locator needs a value")
        return self


class PlanStep(BaseModel):
    kind: Literal["goto", "recorded"]
    url: Optional[str] = None  # goto: a page the tester visited
    action_index: Optional[int] = None  # recorded: index into the recorded actions
    why: str = ""

    @model_validator(mode="after")
    def complete(self):
        if self.kind == "goto" and not (self.url or "").startswith(("http://", "https://")):
            raise ValueError("a goto step needs an absolute http(s) url from the recording")
        if self.kind == "recorded" and self.action_index is None:
            raise ValueError("a recorded step needs action_index")
        return self


class PlanAssertion(BaseModel):
    kind: Literal["visible", "hidden", "text", "value", "url", "no_console_errors", "no_page_errors",
                  "no_failed_requests"]
    locator: Optional[PlanLocator] = None
    expected: Optional[str] = None  # text / value / url fragment
    url_contains: Optional[str] = None  # no_failed_requests: which requests
    why: str = Field(min_length=3)  # ties the check to the oracle

    @model_validator(mode="after")
    def complete(self):
        if self.kind in ("visible", "hidden", "text", "value") and not self.locator:
            raise ValueError(f"a {self.kind} assertion needs a locator")
        if self.kind in ("text", "value", "url") and not (self.expected or "").strip():
            raise ValueError(f"a {self.kind} assertion needs expected")
        return self


class LLMRegressionPlan(BaseModel):
    """AI contract: which recorded steps to replay and what to assert. Mirmera
    renders the code itself, so the AI never writes code."""
    test_title: str = Field(min_length=3)
    steps: list[PlanStep] = Field(min_length=1)
    assertions: list[PlanAssertion] = Field(min_length=1)
    assumptions: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def drop_incomplete_assertions(cls, data):
        return drop_incomplete_assertions(data)


def drop_incomplete_assertions(data):
    """One incomplete check (a url check without the URL, a text check without a
    locator) should not throw away a reply whose other checks are fine, which
    smaller local models often produce. Incomplete checks are dropped and listed
    in assumptions, so the tester sees what is missing. If no check survives,
    validation fails as before and the AI is asked again."""
    if not isinstance(data, dict) or not isinstance(data.get("assertions"), list):
        return data
    kept, dropped = [], []
    for raw in data["assertions"]:
        try:
            PlanAssertion.model_validate(raw)
            kept.append(raw)
        except ValidationError as err:
            reason = err.errors()[0]["msg"].removeprefix("Value error, ")
            why = raw.get("why") if isinstance(raw, dict) else None
            dropped.append(f"Mirmera dropped a check the AI left incomplete ({reason})"
                           + (f": {why}" if why else "") + ". Add it by hand if it matters.")
    if not kept or not dropped:
        return data
    assumptions = data.get("assumptions") if isinstance(data.get("assumptions"), list) else []
    return {**data, "assertions": kept, "assumptions": [*assumptions, *dropped]}


class LLMRegressionChecks(BaseModel):
    """AI contract when the steps are fixed (picked by Mirmera or edited by the
    tester): only what to assert afterwards."""
    test_title: str = Field(min_length=3)
    assertions: list[PlanAssertion] = Field(min_length=1)
    assumptions: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def drop_incomplete_assertions(cls, data):
        return drop_incomplete_assertions(data)


class BugStep(BaseModel):
    """A step to the bug: open a page, or one recorded-style action."""
    type: Literal["goto", "action"]
    url: Optional[str] = None
    action: Optional[dict] = None
    summary: Optional[str] = None  # filled by the server


class BugStepsOut(BaseModel):
    steps: list[BugStep]
    source: Literal["edited", "recording", "ai"]
    note: str
    anchor: Optional[str] = None  # the failed request the steps lead to
    recorded_actions: int  # actions recorded for this bug (since the previous bug)


class BugStepsUpdate(BaseModel):
    steps: list[dict] = Field(max_length=60)


class RegressionRequest(BaseModel):
    provider: Optional[str] = None
    # test.fail(): the test is expected to fail while the bug exists, so CI stays
    # green and turns red the day the bug is fixed (time to remove the line).
    known_failing: bool = True


class ReplayRequest(BaseModel):
    base_url: Optional[str] = Field(default=None, pattern=r"^https?://")
    browser: Optional[Literal["chromium", "firefox", "webkit"]] = None  # None: the browser the bug was found in (Chromium unless said)


class RegressionTestOut(BaseModel):
    id: str
    bug_id: str
    filename: str
    code: str
    plan: dict
    base_url: Optional[str] = None
    known_failing: bool
    warnings: list[str] = []
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    prompt_version: Optional[str] = None
    replay: Optional[dict] = None  # {"outcome", "steps", "assertions", "base_url", "duration_ms"}
    replayed_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("warnings", mode="before")
    @classmethod
    def none_is_empty(cls, value):
        return value or []


# ---------- reproduction agent ----------

class Symptom(BaseModel):
    """What the agent says it saw. Mirmera checks it itself before believing it."""
    # keyboard: Mirmera's own keyboard tour ("unreachable:Send", "no-focus:Email", "trap:Notes"), checked by tabbing again
    # layout: Mirmera's phone layout check ("scroll", "cut:<text>", "overlap:<a>|<b>", "small:<target>"), checked at 390 px
    kind: Literal["failed_request", "console_error", "page_error", "text_visible", "text_absent", "url", "keyboard", "layout"]
    value: str = Field(min_length=1, max_length=300)  # URL part, message part, or page text
    explanation: str = Field(min_length=3, max_length=600)  # why this is the bug the tester reported


AGENT_KEYS = ("Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "Space")


class AgentAction(BaseModel):
    kind: Literal["goto", "click", "fill", "fill_secret", "select", "check", "uncheck", "press", "wait",
                  "reproduced", "give_up"]
    locator: Optional[PlanLocator] = None
    url: Optional[str] = Field(default=None, max_length=2000)
    value: Optional[str] = Field(default=None, max_length=500)
    secret: Optional[str] = None  # fill_secret: the name of an environment variable, never a value
    key: Optional[str] = None
    symptom: Optional[Symptom] = None
    reason: Optional[str] = Field(default=None, max_length=600)

    @model_validator(mode="after")
    def complete(self):
        k = self.kind
        if k in ("click", "fill", "fill_secret", "select", "check", "uncheck", "press") and not self.locator:
            raise ValueError(f"{k} needs a locator")
        if k == "goto" and not (self.url or "").startswith(("http://", "https://")):
            raise ValueError("goto needs an absolute http(s) url")
        if k in ("fill", "select") and self.value is None:
            raise ValueError(f"{k} needs a value")
        if k == "fill_secret" and not re.fullmatch(r"MIRMERA_SECRET_[A-Z0-9_]{1,60}", self.secret or ""):
            raise ValueError("fill_secret needs a secret name like MIRMERA_SECRET_PASSWORD")
        if k == "press" and self.key not in AGENT_KEYS:
            raise ValueError(f"press key must be one of {', '.join(AGENT_KEYS)}")
        if k == "reproduced" and not self.symptom:
            raise ValueError("reproduced needs the symptom you saw")
        if k == "give_up" and not (self.reason or "").strip():
            raise ValueError("give_up needs a reason")
        return self


class LLMAgentStep(BaseModel):
    """AI contract: one step at a time."""
    thought: str = Field(min_length=1, max_length=600)
    action: AgentAction


class ExploreProblem(BaseModel):
    """Something the exploration agent thinks is a problem. Mirmera checks the symptom
    itself; the rest becomes a bug draft for a tester to review."""
    title: str = Field(min_length=3, max_length=200)
    expected: str = Field(min_length=3, max_length=600)
    actual: str = Field(min_length=3, max_length=600)
    severity: Literal["high", "medium", "low"] = "medium"
    symptom: Symptom
    # Mirmera's own key for "the same problem" when the symptom changes every time (a marker
    # value): set by Mirmera's checks, never by the AI.
    same_as: SkipJsonSchema[Optional[str]] = None
    # The failed request as a curl command (app/curl.py), for the report. Mirmera's own, never the AI's.
    reproduce: SkipJsonSchema[Optional[str]] = None
    # The failure Mirmera made happen to show it ("POST /api/verify answering 500"): its replays
    # can't make it happen again, so it isn't replayed. Mirmera's own, never the AI's.
    fault: SkipJsonSchema[Optional[str]] = None


EXPLORE_KINDS = ("goto", "click", "fill", "fill_secret", "select", "check", "uncheck", "press", "wait",
                 "try", "note", "problem", "done")


class ExploreAction(BaseModel):
    kind: Literal["goto", "click", "fill", "fill_secret", "select", "check", "uncheck", "press", "wait",
                  "try", "note", "problem", "done"]

    @model_validator(mode="before")
    @classmethod
    def forgiving(cls, data):
        """Small models name things their own way: "type" for kind, "CLICK", "text" for what
        to type or note, "url" given as "value". Read what they mean; anything else still fails."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "kind" not in d:
            for alt in ("type", "action", "name"):
                if isinstance(d.get(alt), str) and d[alt].strip().lower() in EXPLORE_KINDS:
                    d["kind"] = d.pop(alt)
                    break
        if isinstance(d.get("kind"), str):
            d["kind"] = d["kind"].strip().lower().replace("-", "_").replace(" ", "_")
        k = d.get("kind")
        text = next((d[x] for x in ("text", "input", "content", "message") if isinstance(d.get(x), str)), None)
        if k in ("fill", "select") and d.get("value") is None and text is not None:
            d["value"] = text
        if k == "note" and not d.get("note") and (text or d.get("value")):
            d["note"] = text or d.get("value")
        if k == "done" and not d.get("summary") and (text or d.get("note")):
            d["summary"] = text or d.get("note")
        if k == "goto" and not d.get("url") and isinstance(d.get("value"), str) and d["value"].startswith(("http", "/")):
            d["url"] = d.pop("value")
        if k in ("click", "fill", "select", "check", "uncheck", "press", "fill_secret") and "locator" not in d \
                and isinstance(d.get("target"), dict):
            d["locator"] = d.pop("target")
        loc = d.get("locator")
        if k in ("fill", "select") and d.get("value") is None and isinstance(loc, dict) \
                and str(loc.get("method", "")).lower() != "role" and loc.get("name") and loc.get("value"):
            # {"locator": {"method": "label", "name": "Expiry date", "value": "2020-01-01"}}: the field and what to type
            d["value"], d["locator"] = loc["value"], {**loc, "value": loc["name"]}
        field_name = d.get("key") if isinstance(d.get("key"), str) and d.get("key") not in AGENT_KEYS else None
        if k in ("fill", "select", "check", "uncheck") and not d.get("locator") and (field_name or d.get("field")):
            # {"kind": "select", "key": "documentType", "value": "Passport"}: the field by its name
            d["locator"] = {"method": "label", "value": field_name or d.get("field")}
            d.pop("key", None)
        if k == "try":
            vals = next((d[x] for x in ("values", "fields", "inputs", "data", "form") if isinstance(d.get(x), dict)), None)
            if vals is None and isinstance(loc, dict) and d.get("value") is not None:
                vals = {loc.get("value") or loc.get("name") or "": d["value"]}
            d["values"] = {str(f): ("" if v is None else str(v)) for f, v in (vals or {}).items() if str(f).strip()}
        if k in ("fill", "select") and d.get("value") is None and d.get("locator"):
            d["pick"] = True  # no value given: Mirmera picks the next test idea for that field
        return d
    locator: Optional[PlanLocator] = None
    values: dict[str, str] = {}  # try: field label -> value; every other field gets ordinary valid data
    pick: SkipJsonSchema[bool] = False  # fill/select without a value: Mirmera fills in the next test idea (not for the AI to set)
    url: Optional[str] = Field(default=None, max_length=2000)
    value: Optional[str] = Field(default=None, max_length=500)
    secret: Optional[str] = None
    key: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=600)  # note: what was tried or noticed
    problem: Optional[ExploreProblem] = None
    summary: Optional[str] = Field(default=None, max_length=1200)  # done: what was covered, what wasn't

    @model_validator(mode="after")
    def complete(self):
        k = self.kind
        if k == "note" and not (self.note or "").strip():
            raise ValueError("note needs the note text")
        if k == "problem" and not self.problem:
            raise ValueError("problem needs title, expected, actual and the symptom")
        if k == "done" and not (self.summary or "").strip():
            raise ValueError("done needs a summary of what was covered")
        if k == "try" and len(self.values) > 12:
            raise ValueError("try changes a few fields at most (the rest are filled with valid data)")
        if k not in ("note", "problem", "done", "try") and not self.pick:
            self.as_browser_action()  # the same checks as the reproduction agent's actions
        return self

    def as_browser_action(self) -> "AgentAction":
        return AgentAction(kind=self.kind, locator=self.locator, url=self.url, value=self.value,
                           secret=self.secret, key=self.key)


class LLMExploreJudgement(BaseModel):
    """AI contract for checking one answer: plain facts only. Mirmera compares them and
    decides itself whether the answer breaks the rule (see agent/explore.py: decide)."""
    answer_means: Literal["accepted", "refused", "error", "other"]
    answer_quote: str = Field(default="", max_length=400)  # copied from the answer; checked
    rule: Optional[str] = Field(default=None, max_length=20)  # "AC2", "R3", or null
    rule_expects: Literal["accept", "refuse", "none"] = "none"
    because: str = Field(default="", max_length=600)
    title: Optional[str] = Field(default=None, max_length=200)

    @model_validator(mode="before")
    @classmethod
    def forgiving(cls, data):
        """Words small models use for the same facts: "rejected", "success", "Refuse", "AC 2"."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        means = {"accept": "accepted", "accepted": "accepted", "success": "accepted", "succeeded": "accepted", "verified": "accepted",
                 "refuse": "refused", "refused": "refused", "rejected": "refused", "reject": "refused", "denied": "refused",
                 "validation_error": "refused", "error": "error", "failed": "error", "failure": "error", "crash": "error",
                 "other": "other", "unclear": "other", "unknown": "other"}
        expects = {"accept": "accept", "accepted": "accept", "allow": "accept", "refuse": "refuse", "refused": "refuse",
                   "reject": "refuse", "rejected": "refuse", "deny": "refuse", "none": "none", "n/a": "none", "null": "none"}
        if isinstance(d.get("answer_means"), str):
            d["answer_means"] = means.get(d["answer_means"].strip().lower().replace(" ", "_"), d["answer_means"])
        if isinstance(d.get("rule_expects"), str):
            d["rule_expects"] = expects.get(d["rule_expects"].strip().lower(), d["rule_expects"])
        elif d.get("rule_expects") is None:
            d["rule_expects"] = "none"
        if isinstance(d.get("rule"), str):
            d["rule"] = re.sub(r"\s+", "", d["rule"]).upper() or None
        return d


class LLMExploreStep(BaseModel):
    """AI contract for the exploration agent: one step at a time."""
    thought: str = Field(default="(no reason given)", max_length=600)
    action: ExploreAction

    @model_validator(mode="before")
    @classmethod
    def forgiving(cls, data):
        """A flat reply ({"thought", "kind", "locator"…}), or "action": "click" with the rest
        beside it, is one step all the same. A done without a summary uses the thought."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if not isinstance(d.get("action"), dict):
            rest = {k: v for k, v in d.items() if k not in ("thought", "reasoning", "action")}
            if isinstance(d.get("action"), str):
                rest["kind"] = d["action"]
            if rest:
                d = {"thought": d.get("thought") or d.get("reasoning"), "action": rest}
        if not d.get("thought"):
            d["thought"] = d.pop("reasoning", None) or "(no reason given)"
        action = d.get("action")
        if isinstance(action, dict) and str(action.get("kind", action.get("type", ""))).lower() == "done" \
                and not action.get("summary") and not any(isinstance(action.get(x), str) for x in ("text", "note", "message")):
            d["action"] = {**action, "summary": d["thought"]}
        return d


class AppSignIn(BaseModel):
    """How Mirmera signs in to the app under test. password: None keeps the saved one, "" clears it."""
    url: str = Field(min_length=1, max_length=500)  # the sign-in page: a path ("/login") or a full address
    username_field: str = Field(default="", max_length=120)  # its label, id, name or placeholder
    username: str = Field(default="", max_length=200)
    password_field: str = Field(default="Password", max_length=120)
    password: Optional[str] = Field(default=None, max_length=500)
    submit: str = Field(default="", max_length=120)  # the button's text; empty: the form's own button, or Enter
    signed_in_text: str = Field(default="", max_length=200)  # words that show it worked, e.g. "Sign out"
    # A second user, for checking who sees what (their own things, not the first user's)
    second_username: str = Field(default="", max_length=200)
    second_password: Optional[str] = Field(default=None, max_length=500)

    @field_validator("url")
    @classmethod
    def a_page_of_the_app(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("/") or re.match(r"^https?://", v)):
            raise ValueError("the sign-in page is a path like /login or an address starting with http:// or https://")
        return v


class AppSignInOut(BaseModel):
    url: str
    username_field: str
    username: str
    password_field: str
    password_set: bool
    submit: str
    signed_in_text: str
    second_username: str = ""
    second_password_set: bool = False


class AppSignInTry(BaseModel):
    ok: bool
    detail: str
    url: str
    screenshot: Optional[str] = None  # a data: URL of what the page showed after signing in


class ExploreConditions(BaseModel):
    """How the agent's browser is set up, like a tester picking up another device."""
    device: Literal["desktop", "phone"] = "desktop"
    network: Literal["normal", "slow"] = "normal"
    locale: Optional[str] = Field(default=None, pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")  # e.g. "am-ET"
    timezone: Optional[str] = Field(default=None, pattern=r"^[A-Za-z_]+(/[A-Za-z_+\-0-9]+)*$")  # e.g. "Africa/Addis_Ababa"
    # The browser's clock: a moment at the calendar's edge ("after-midnight", "pagume", "ethiopian-new-year",
    # "before-midnight", "year-end", "leap-day") or a local date and time ("2026-09-10T23:50").
    clock: Optional[str] = Field(default=None, max_length=40)
    browser: Optional[Literal["chromium", "firefox", "webkit"]] = None  # the engine: Chromium unless asked

    @field_validator("clock")
    @classmethod
    def _known_clock(cls, v: Optional[str]) -> Optional[str]:
        if v:
            from app.agent.clock import resolve
            resolve(v)  # raises with the presets listed
        return v or None


class ExploreRequest(BaseModel):
    base_url: Optional[str] = Field(default=None, pattern=r"^https?://")  # default: the project's app URL
    provider: Optional[str] = None
    max_steps: int = Field(default=25, ge=3, le=60)
    conditions: Optional[ExploreConditions] = None


class ExplorationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    charter_id: str
    session_id: Optional[str] = None
    status: str
    start_url: str
    max_steps: int
    steps: list[dict] = []
    bug_ids: list[str] = []
    summary: Optional[str] = None
    detail: Optional[str] = None
    started_by: Optional[str] = None
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    video: Optional[str] = None  # set when its recording was kept: GET /exploration-runs/{id}/video
    hints: list[dict] = []  # what a tester (or Mirmera) asked it to try next, and the step that took it up
    conditions: Optional[dict] = None  # device, network, locale, timezone it browsed with
    created_at: datetime
    finished_at: Optional[datetime] = None

    @field_validator("hints", mode="before")
    @classmethod
    def none_is_no_hints(cls, value):
        return value or []


class ExploreHint(BaseModel):
    """A tester pairing with the agent: what to try next, in plain words."""
    text: str = Field(min_length=2, max_length=400)


class ReproduceRequest(BaseModel):
    replay_known_steps: bool = True  # replay Steps to the bug first (no AI); the AI explores only if needed
    base_url: Optional[str] = Field(default=None, pattern=r"^https?://")
    provider: Optional[str] = None
    max_steps: int = Field(default=20, ge=3, le=40)
    runs: int = Field(default=3, ge=1, le=10)  # reruns of the found steps, to measure how reliably it reproduces


class ReproductionRunOut(BaseModel):
    id: str
    bug_id: str
    status: str  # running | reproduced | not_reproduced | cancelled | error
    base_url: str
    start_url: str
    max_steps: int
    runs: int
    steps: list[dict] = []
    symptom: Optional[dict] = None
    detail: Optional[str] = None
    reproducibility: Optional[dict] = None  # {"runs", "reproduced", "results": [...]}
    plan: Optional[dict] = None
    has_screenshot: bool = False
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    prompt_version: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def flags(cls, data):
        if hasattr(data, "screenshot_path"):
            out = {c: getattr(data, c) for c in cls.model_fields if c != "has_screenshot" and hasattr(data, c)}
            out["has_screenshot"] = bool(data.screenshot_path)
            out["steps"] = data.steps or []
            return out
        return data


# ---------- Chrome extension ----------

class ActiveSessionOut(BaseModel):
    id: str
    charter_mission: str
    started_at: datetime
    paused: bool
    project_id: str
    project_name: str


class ExtensionEvent(BaseModel):
    """One thing the extension saw in the recorded tab. The server builds the
    summary itself from these fields, and redacts URLs."""
    kind: Literal["navigation", "action", "console_error", "page_error", "failed_request"]
    url: Optional[str] = Field(default=None, max_length=4000)
    message: Optional[str] = Field(default=None, max_length=4000)  # console / page errors
    method: Optional[str] = Field(default=None, max_length=16)  # failed requests
    status: Optional[int] = None  # failed requests: HTTP status, or none when there was no response
    error: Optional[str] = Field(default=None, max_length=300)  # failed requests without a response
    action: Optional[dict] = None  # recorded actions, as recorder.js sends them


class ExtensionEvents(BaseModel):
    events: list[ExtensionEvent] = Field(max_length=200)


class ExtensionScreenshot(BaseModel):
    image: str = Field(max_length=8_000_000)  # data URL from chrome.tabs.captureVisibleTab (PNG or JPEG)
    caption: Optional[str] = Field(default=None, max_length=500)
    bug_id: Optional[str] = None
    url: Optional[str] = Field(default=None, max_length=4000)


# ---------- isolate the bug ----------

class IsolationSymptom(BaseModel):
    """What shows the bug: a request that fails, or a text on the page."""
    kind: Literal["failed_request", "text"]
    value: str = Field(min_length=1, max_length=300)

    @field_validator("value")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("is required")
        return v


class IsolateRequest(BaseModel):
    base_url: Optional[str] = Field(default=None, pattern=r"^https?://")
    symptom: Optional[IsolationSymptom] = None  # default: the failed request, else the report's "actual"


class IsolationRunOut(BaseModel):
    id: str
    bug_id: str
    status: str
    base_url: str
    symptom: dict
    baseline: Optional[dict] = None
    variants: list[dict] = []
    total: int
    summary: Optional[dict] = None
    detail: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("variants", mode="before")
    @classmethod
    def _none_is_empty(cls, v):
        return v or []


class Deleted(BaseModel):
    """What a delete removed, e.g. {"sessions": 2, "bugs": 3, "evidence": 140}."""
    deleted: dict[str, int]


# ---------- regression suite ----------

class SuiteRequest(BaseModel):
    base_url: Optional[str] = Field(default=None, pattern=r"^https?://")  # None: each test's recorded address
    browser: Optional[Literal["chromium", "firefox", "webkit"]] = None  # None: each test in the browser its bug was found in


class SuiteTest(BaseModel):
    """A bug's latest regression test, for the project's Regression page."""
    bug_id: str
    bug_title: str
    test_id: str
    filename: str
    known_failing: bool
    base_url: Optional[str] = None
    outcome: Optional[str] = None  # last replay
    meaning: Optional[str] = None
    replayed_at: Optional[datetime] = None
    created_at: datetime
    flaky: Optional[dict] = None  # its result flips run to run: {"flips", "runs", "pattern", "summary"}


class SuiteRunOut(BaseModel):
    id: str
    project_id: str
    status: str
    base_url: Optional[str] = None
    browser: Optional[str] = None
    total: int
    results: list[dict] = []
    detail: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("results", mode="before")
    @classmethod
    def _none_is_empty(cls, v):
        return v or []


class RetestRequest(BaseModel):
    base_url: Optional[str] = Field(default=None, pattern=r"^https?://")  # None: each test's recorded address


class RetestOut(BaseModel):
    id: str
    project_id: str
    bug_id: str
    bug_title: str = ""
    filing_id: str
    tracker: str
    issue_key: str
    issue_url: str
    test_id: Optional[str] = None
    outcome: str  # running | fixed | still_fails | could_not_check | no_test
    replay: Optional[dict] = None
    detail: Optional[str] = None
    commented: bool = False
    comment_error: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class RetestCheckOut(BaseModel):
    checked: int  # issues looked up
    filed: int  # issues filed from the project
    retests: list[RetestOut]  # one per issue closed since the last check
    problems: list[str]  # issues that couldn't be looked up, and why


class MockIssueState(BaseModel):
    tracker: Literal["github", "jira"]
    target: str
    issue_key: str
    closed: bool = True


class MockIssueCreate(BaseModel):
    tracker: Literal["github", "jira"]
    target: str
    title: str
    body: str = ""
    sprints: Optional[list[dict]] = None  # Jira's sprint field, as Jira sends it


class ProjectReport(BaseModel):
    """The test report as blocks: heading, paragraph, table, bullets, quote."""
    project_id: str
    project_name: str
    since: Optional[date] = None
    until: Optional[date] = None
    period: str
    generated_at: datetime
    summary: dict
    glance: list[dict] = []  # [{"tone", "title", "detail", "label", "to"}]: what matters most, where to act
    charts: dict = {}  # {"severity": {...}, "risks": {...}, "time": {"seconds", "split"}}
    top_bugs: dict = {}  # {"headers", "rows", "total", "bugs", "questions"}
    sections: list[dict] = []  # [{"key", "title", "counts", "open", "parts": [{"title", "count", "open", "blocks"}]}]
    ai_summary: Optional["ReportSummaryOut"] = None  # kept by the tester for this period
    blocks: list[dict]  # the full report, flat


class CoveragePage(BaseModel):
    path: str  # ids collapsed: /orders/:id
    hosts: list[str]
    sessions: int
    testers: list[str]
    charters: list[str]
    visits: int
    actions: int
    controls: list[str]  # 'click button "Verify"', without values typed
    problems: list[dict]  # [{"summary", "count"}]
    bugs: list[dict]  # [{"id", "title", "severity", "kind"}] logged while on this page
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    flags: list[str]  # not_tried | problems_without_bug | stale | one_tester


class CoverageArea(BaseModel):
    category: str
    name: str
    description: str = ""
    risks: list[str]
    sessions: int
    last_tested: Optional[datetime] = None


class CoverageMap(BaseModel):
    project_id: str
    since: Optional[datetime] = None
    generated_at: datetime
    sessions: int
    recorded_sessions: int  # sessions with a recorded browser or the extension
    stale_days: int
    pages: list[CoveragePage]
    areas: list[CoverageArea]
    gaps: list[str]


class NudgeOut(BaseModel):
    id: str
    kind: str  # problem | risk | idea | heuristic | field | choice | upload | submit | page | time
    title: str
    why: str
    heuristic: str
    heuristic_key: Optional[str] = None  # its card in the heuristics library
    suggestions: list[str]
    priority: int


class NudgeTried(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class AIProviderStatus(BaseModel):
    name: str
    configured: bool
    model: Optional[str] = None
    problem: Optional[str] = None


class AITask(BaseModel):
    key: str
    label: str
    hint: str


class AIOverview(BaseModel):
    tasks: list[AITask]
    providers: list[AIProviderStatus]


class AICheckRequest(BaseModel):
    provider: str
    task: str
    project_id: Optional[str] = None  # check with this project's workspace keys


class AICheckResult(BaseModel):
    """One task tried once on one provider, with made-up but realistic input."""
    provider: str
    model: Optional[str] = None
    task: str
    result: Literal["ok", "retried", "failed"]  # ok: valid first time; retried: needed the second try
    seconds: float
    sample: Optional[str] = None  # a glimpse of the reply, to judge its quality
    detail: Optional[str] = None


# ---------- AI-drafted report summary ----------

class LLMReportSummary(BaseModel):
    summary: str = Field(min_length=20, max_length=900)
    basis: list[str] = []  # fact lines the summary relies on, copied from the input


class ReportSummaryDraft(BaseModel):
    text: str
    basis: list[str]
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    prompt_version: str


class ReportSummaryIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    prompt_version: Optional[str] = None
    edited: bool = False  # the tester changed the AI's words


class ReportSummaryRequest(BaseModel):
    provider: Optional[str] = None  # override the project's choice for this task


class ReportSummaryOut(BaseModel):
    text: str
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    prompt_version: Optional[str] = None
    edited: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CharterHeuristics(BaseModel):
    keys: list[str] = Field(default=[], max_length=20)


class Trends(BaseModel):
    """Week by week (Monday to Sunday, UTC), oldest first."""
    project_id: str
    weeks: int
    generated_at: datetime
    phases: dict  # time-split keys and their names
    rows: list[dict]  # [{"week", "sessions", "hours", "testers", "bugs": {...}, "bugs_per_hour", "time_split", "charters", "fixes", "risk_exposure"}]
    changes: list[dict]  # the last 4 weeks against the 4 before: [{"name", "before", "recent", "direction", "text", "note"}]


class NotificationSettings(BaseModel):
    telegram_chat_id: str = Field(default="", max_length=64)  # e.g. 123456789, or -100… for a group
    slack: bool = False
    events: list[str] = []


class NotificationLogOut(BaseModel):
    event: str
    channel: str
    text: str
    status: str  # sent | failed | mock
    error: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationsOut(BaseModel):
    events: list[dict]  # [{"key", "label"}]
    settings: NotificationSettings
    channels: dict  # {"telegram": {"configured", "problem"}, "slack": {...}, "mock": bool}
    recent: list[NotificationLogOut]


# ---- User stories ------------------------------------------------------------

class StoryCreate(BaseModel):
    """A story written here, for teams whose tracker isn't connected."""
    title: str = Field(min_length=3, max_length=300)
    key: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = Field(default=None, max_length=20_000)
    url: Optional[str] = Field(default=None, max_length=500)


class StoryImport(BaseModel):
    ref: str = Field(min_length=1, max_length=500)  # KYC-12, #45, or an address


class StoryUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=3, max_length=300)
    key: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = Field(default=None, max_length=20_000)
    url: Optional[str] = Field(default=None, max_length=500)
    closed: Optional[bool] = None
    sprint_id: Optional[str] = None  # null takes the story out of its sprint


class StoryLinks(BaseModel):
    ids: list[str] = Field(default=[], max_length=200)


class StorySummary(BaseModel):
    id: str
    key: Optional[str]
    title: str
    url: Optional[str]
    tracker: Optional[str]
    tracker_status: Optional[str]
    closed: bool
    created_at: datetime
    risks: int
    charters: int
    sessions: int
    minutes: int
    bugs: int
    high_bugs: int
    left: int
    sprint: Optional[dict] = None  # {"id", "name"}


class StoryDetail(StorySummary):
    description: Optional[str]
    artifact_id: Optional[str]
    risk_list: list[dict]  # [{"id", "ref", "title", "failure_mode", "score", "status", "quality_criterion", "charters", "other_stories"}]
    charter_list: list[dict]  # [{"id", "mission_statement", "status", "priority", "source", "rationale", "risk_refs", "sessions", "minutes", "bugs"}]
    session_list: list[dict]  # [{"id", "charter_id", "mission_statement", "tester", "started_at", "ended_at", "minutes", "bugs", "debrief"}]
    bug_list: list[dict]  # [{"id", "title", "kind", "severity", "session_id", "issue_key", "issue_url"}]
    pages: list[dict]  # [{"page", "sessions", "actions"}]
    more_pages: int
    left_list: list[dict]  # [{"text", "to", "action"}]
    sprint_note: Optional[str] = None  # after an import or refresh: what happened to the Jira sprint


# ---- Sprints ---------------------------------------------------------------------

class SprintCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    goal: Optional[str] = Field(default=None, max_length=5000)
    starts_on: date
    ends_on: date
    story_ids: list[str] = Field(default=[], max_length=200)

    @model_validator(mode="after")
    def ends_after_start(self):
        if self.ends_on < self.starts_on:
            raise ValueError("The sprint must end on or after the day it starts.")
        return self


class SprintUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    goal: Optional[str] = Field(default=None, max_length=5000)
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None


class SprintSummary(BaseModel):
    id: str
    name: str
    goal: Optional[str]
    starts_on: date
    ends_on: date
    state: Literal["upcoming", "current", "past"]
    days_left: Optional[int]
    stories: int
    stories_done: int
    sessions: int
    minutes: int
    bugs: int
    left: int


class SprintDetail(SprintSummary):
    story_list: list[StorySummary]
    under_stories: dict  # {"sessions", "minutes", "bugs"}: testing under the sprint's stories, in its dates
    outside_stories: dict  # the same for everything else tested in its dates
    outside_charters: list[dict]  # [{"id", "mission_statement", "sessions", "minutes", "stories"}]
    high_unexplored: list[dict]  # [{"id", "ref", "title", "score", "in_sprint"}]
    left_list: list[dict]  # [{"text", "to", "action"}]


# ---- Accounts ----------------------------------------------------------------------

class SignUp(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=1, max_length=200)
    workspace_name: Optional[str] = Field(default=None, max_length=100)


class SignIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class AccountUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class Me(BaseModel):
    user: dict  # {"id", "name", "email"}
    workspaces: list[dict]  # [{"id", "name", "role"}]


class AuthConfig(BaseModel):
    allow_signups: bool
    has_users: bool
    unowned_projects: int  # projects the first account will take over


class ApiTokenCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)


class ApiTokenOut(BaseModel):
    id: str
    label: Optional[str]
    created_at: datetime
    last_used_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class ApiTokenCreated(ApiTokenOut):
    token: str  # shown once


# ---- Members and invitations -----------------------------------------------------------

Role = Literal["viewer", "tester", "admin", "owner"]


class InvitationIn(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Role = "tester"


class InvitationCreated(BaseModel):
    id: str
    email: str
    role: str
    link: str  # for the admin to send when email isn't set up
    emailed: bool


class InvitationView(BaseModel):
    workspace_name: str
    email: str
    role: str
    invited_by: Optional[str]
    has_account: bool


class InvitedSignUp(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class RoleChange(BaseModel):
    role: Role


class MembersOut(BaseModel):
    workspace: dict  # {"id", "name"}
    members: list[dict]  # [{"user_id", "name", "email", "role", "joined_at"}]
    invitations: list[dict]  # [{"id", "email", "role", "created_at", "expires_at", "expired", "invited_by"}]
    email_configured: bool


class ForgotPassword(BaseModel):
    email: str = Field(max_length=254)


class ResetPassword(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=200)


# ---- Workspace keys ----------------------------------------------------------------------

class WorkspaceKeysIn(BaseModel):
    values: dict[str, Optional[str]] = Field(default={}, max_length=20)  # name -> value; empty removes


class WorkspaceKeysOut(BaseModel):
    keys: list[dict]  # [{"name", "label", "group", "secret", "hint", "set", "shown", "unreadable", "updated_at", "updated_by", "server_fallback"}]
    server_keys_for_all: bool


# ---- API exploration --------------------------------------------------------------

ApiMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


class ApiHeader(BaseModel):
    name: str = Field(max_length=200)
    value: str = Field(max_length=8000)


class ApiRequestIn(BaseModel):
    method: ApiMethod = "GET"
    url: str = Field(min_length=1, max_length=8000)
    headers: list[ApiHeader] = Field(default=[], max_length=50)
    body: Optional[str] = Field(default=None, max_length=1_000_000)
    base_url: Optional[str] = Field(default=None, max_length=2000)  # for a path like /api/x; default: the project's app URL
    source: Literal["manual", "curl", "recorded", "openapi", "variation"] = "manual"


class ApiRequestDraft(BaseModel):
    method: str
    url: str
    headers: list[ApiHeader]
    body: Optional[str]


class RecordedRequest(ApiRequestDraft):
    seen: str  # what the browser recorded, e.g. "POST https://… → 500"
    count: int
    kind: str  # failed_request | slow_request


class CurlIn(BaseModel):
    command: str = Field(min_length=1, max_length=200_000)


class ApiResponseOut(BaseModel):
    url: str
    status: Optional[int]
    reason: Optional[str]
    ms: int
    size: int
    headers: list[ApiHeader]
    content_type: str
    body: Optional[str]
    truncated: bool
    error: Optional[str]


class ApiSendOut(BaseModel):
    evidence: EvidenceOut
    response: ApiResponseOut


# ---- OpenAPI ------------------------------------------------------------------------

class OpenApiImport(BaseModel):
    url: Optional[str] = Field(default=None, max_length=2000)
    text: Optional[str] = Field(default=None, max_length=5_000_000)


class OpenApiSpecOut(BaseModel):
    id: str
    label: str
    title: str
    version: str
    servers: list[str]
    operations: int
    source: Optional[str]  # the address it was imported from, or None when pasted
    imported_at: datetime


class OpenApiEndpoint(BaseModel):
    key: str  # "GET /documents/{id}"
    method: str
    path: str
    url: str  # the path with example values filled in where the spec gives them
    summary: str
    tag: str
    deprecated: bool
    auth: list[str]  # "bearer token", "API key in header X-Api-Key"...
    headers: list[ApiHeader]
    body: Optional[str]
    unfilled: list[str]  # path parameters still to fill in, e.g. ["id"]
    tried: int  # requests this project's sessions sent to it
    last_status: Optional[int]


class OpenApiOperationsOut(OpenApiSpecOut):
    endpoints: list[OpenApiEndpoint]
    tried: int  # endpoints tried at least once


# ---- Test strategy -------------------------------------------------------------------

class LLMStrategy(BaseModel):
    sections: dict[str, str]  # section key -> draft text


class StrategySection(BaseModel):
    key: str
    title: str
    hint: str


class StrategyVersionOut(BaseModel):
    id: str
    version: int
    sprint_id: Optional[str]
    sprint_name: Optional[str]
    sections: dict[str, str]
    ai_provider: Optional[str]
    ai_model: Optional[str]
    author: Optional[str]
    created_at: datetime


class StrategyOverview(BaseModel):
    sections: list[StrategySection]
    versions: list[StrategyVersionOut]  # newest first, for this focus
    draft: dict[str, str]  # from the facts, for a fresh start
    facts: dict[str, list[str]]  # what Mirmera knows, per section


class StrategyAIRequest(BaseModel):
    sprint_id: Optional[str] = None
    provider: Optional[str] = None


class StrategyAIDraft(BaseModel):
    sections: dict[str, str]
    ai_provider: str
    ai_model: Optional[str]
    prompt_version: str


class StrategySave(BaseModel):
    sprint_id: Optional[str] = None
    sections: dict[str, str]
    ai_provider: Optional[str] = Field(default=None, max_length=40)
    ai_model: Optional[str] = Field(default=None, max_length=120)
    prompt_version: Optional[str] = Field(default=None, max_length=40)


# ---------- the Mirmera app for Jira ----------

class JiraInstallationOut(BaseModel):
    id: str
    site_url: Optional[str] = None
    cloud_id: Optional[str] = None
    environment: Optional[str] = None
    linked_by: Optional[str] = None  # the Mirmera admin who made the code
    linked_at: datetime
    last_seen_at: Optional[datetime] = None


class JiraAgentTaskOut(BaseModel):
    id: str
    issue_key: Optional[str] = None
    issue_title: Optional[str] = None
    invocation: Optional[str] = None
    action: Optional[str] = None  # plan | status
    state: str
    message: Optional[str] = None
    project_id: Optional[str] = None
    story_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class JiraAppOverview(BaseModel):
    configured: bool  # JIRA_APP_ID is set on this server
    installations: list[JiraInstallationOut]
    agent_tasks: list[JiraAgentTaskOut] = []  # the latest work Jira gave the Mirmera agent


class JiraLinkCodeOut(BaseModel):
    code: str  # shown once
    expires_at: datetime


class JiraLinkIn(BaseModel):
    code: str = Field(min_length=8, max_length=200)
    site_url: Optional[str] = Field(default=None, max_length=300)


class JiraAppStatus(BaseModel):
    linked: bool
    workspace: Optional[str] = None
    site_url: Optional[str] = None
    linked_at: Optional[datetime] = None
    app_url: Optional[str] = None


class JiraIssuePanelIn(BaseModel):
    issue_key: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z][A-Za-z0-9_]+-\d+$")
    project_key: Optional[str] = Field(default=None, max_length=30)


class JiraPanelStory(BaseModel):
    id: str
    project_id: str
    project: str
    title: str
    risks: int
    charters: int
    sessions: int
    minutes: int
    bugs: int
    high_bugs: int
    left: list[str]
    sprint: Optional[str] = None
    url: Optional[str] = None


class JiraPanelBug(BaseModel):
    id: str
    project_id: str
    project: str
    title: str
    kind: Optional[str] = None
    severity: Optional[str] = None
    filed_as: str
    found_in: Optional[str] = None
    retest: Optional[str] = None
    url: Optional[str] = None


class JiraPanelProject(BaseModel):
    id: str
    name: str
    stories_url: Optional[str] = None


class JiraIssuePanel(BaseModel):
    issue_key: str
    workspace: str
    stories: list[JiraPanelStory]
    bugs: list[JiraPanelBug]
    projects: list[JiraPanelProject]
    app_url: Optional[str] = None


class JiraPlanIn(BaseModel):
    issue_key: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z][A-Za-z0-9_]+-\d+$")
    summary: Optional[str] = Field(default=None, max_length=1000)
    description: Optional[object] = None  # plain text, or Jira's document format (ADF)


class JiraPlanOut(BaseModel):
    id: str
    state: str
    done: bool
    ok: bool
    message: Optional[str] = None
    story_url: Optional[str] = None


# ---------- what Mirmera's agents are doing ----------

class AgentStage(BaseModel):
    key: str
    label: str
    status: str  # done | active | todo | skipped | failed | cancelled


class AgentExploration(BaseModel):
    run_id: str
    status: str
    charter_id: str
    mission: str
    session_id: Optional[str] = None
    steps_used: int
    max_steps: int
    actions: int
    recent: list[dict]  # the last few steps: n, thought, action, kind, status, detail
    bugs: list[dict]  # bug drafts: id, title, severity
    summary: Optional[str] = None
    detail: Optional[str] = None


class AgentActivity(BaseModel):
    id: str
    kind: Literal["jira", "explore", "ai"]
    state: str  # running | done | failed | cancelled
    stage: str
    title: str
    issue_key: Optional[str] = None
    project_id: Optional[str] = None
    story_id: Optional[str] = None
    started_by: Optional[str] = None
    stages: list[AgentStage]
    exploration: Optional[AgentExploration] = None
    message: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None


class AgentActivityList(BaseModel):
    items: list[AgentActivity]
    running: int


# ---------- AI jobs: AI steps that survive a refresh ----------

class AiJobCreate(BaseModel):
    kind: Literal["product_model", "risk_analysis", "charters_from_risks", "app_map", "story_risks",
                  "story_charters", "charter_suggestions"]
    request: dict = Field(default_factory=dict)


class AiJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    kind: str
    label: str = ""
    status: str
    request: dict = {}
    result: Optional[Any] = None
    error: Optional[str] = None
    started_by: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None


class AppMapRequest(BaseModel):
    """Survey the app: follow its links (same site), read its pages and forms, change nothing."""
    base_url: Optional[str] = Field(default=None, pattern=r"^https?://")
    max_pages: int = Field(default=25, ge=1, le=60)


class CompareSide(Environment):
    """One side of a comparison: an address, in a browser (Chromium unless said)."""
    browser: Literal["chromium", "firefox", "webkit"] = "chromium"


class CompareRequest(BaseModel):
    """Do a session's journey on two versions or environments (or one address in two browsers)
    and list what differs."""
    a: CompareSide
    b: CompareSide

    @model_validator(mode="after")
    def _two(self):
        if self.a.url.rstrip("/") == self.b.url.rstrip("/") and self.a.browser == self.b.browser:
            raise ValueError("Pick two different addresses, or two browsers, to compare.")
        if self.a.name == self.b.name:
            raise ValueError("Give the two environments different names.")
        return self


class ComparisonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    session_id: str
    env_a: dict
    env_b: dict
    steps: list[dict] = []
    summary: Optional[str] = None
    status: str
    detail: Optional[str] = None
    started_by: Optional[str] = None
    bug_ids: list[str] = []
    created_at: datetime


class CompareBugRequest(BaseModel):
    """Draft a bug from one differing step: the environment that is right is the oracle."""
    right: Literal["a", "b"] = "a"


class DismissRequest(BaseModel):
    """Why a draft isn't a bug: Mirmera doesn't draft the same problem again in this project."""
    reason: Literal["by_design", "not_a_problem", "not_important", "duplicate", "cant_reproduce"]
    note: Optional[str] = Field(default=None, max_length=1000)


class DismissalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    bug_id: Optional[str] = None
    fingerprint: str
    title: str
    reason: str
    note: Optional[str] = None
    by: Optional[str] = None
    created_at: datetime


class TestDataIn(BaseModel):
    """A value the team keeps for the app under test."""
    __test__ = False
    name: str = Field(min_length=1, max_length=80, pattern=r"^[^{}]+$")
    value: Optional[str] = Field(default=None, max_length=2000)  # None on an update: keep the stored one
    secret: bool = False
    labels: list[str] = Field(default_factory=list, max_length=12)
    page: Optional[str] = Field(default=None, max_length=300)
    kind: Literal["valid", "invalid", "edge"] = "valid"
    once: bool = False
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("labels")
    @classmethod
    def _labels(cls, v: list[str]) -> list[str]:
        out = []
        for x in v:
            x = x.strip()[:80]
            if x and x.lower() not in {o.lower() for o in out}:
                out.append(x)
        return out


class TestDataOut(BaseModel):
    __test__ = False
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    value: Optional[str] = None  # never for a secret row
    secret: bool
    labels: list[str] = []
    page: Optional[str] = None
    kind: str
    once: bool
    used_at: Optional[datetime] = None
    note: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    missed_at: Optional[datetime] = None
    # A run saw forms but none of this row's fields since it last met one: renamed?
    not_seen: bool = False
    created_at: datetime


class TestDataImport(BaseModel):
    """CSV with a header row: name,value,labels,kind,once,secret,page,note (labels separated by |)."""
    __test__ = False
    csv: str = Field(min_length=1, max_length=200_000)


class KnownField(BaseModel):
    label: str
    type: str
    pages: list[str] = []


class FreshDataOut(BaseModel):
    """A regression test given fresh values where the app said its data was used, and replayed."""
    test: "RegressionTestOut"
    changes: list[dict]  # [{"field", "was", "now"}]
