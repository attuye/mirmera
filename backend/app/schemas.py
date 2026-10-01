"""Pydantic schemas — request/response shapes, and the AI provider's structured output contracts."""
from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---- Products / Artifacts -------------------------------------------------

class ProductCreate(BaseModel):
    name: str
    org_id: str


class ProductOut(BaseModel):
    id: str
    name: str
    org_id: str
    default_ai_provider: str
    testing_mission: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    testing_mission: Optional[str] = None
    default_ai_provider: Optional[str] = None  # validated against the registered providers


class ArtifactCreate(BaseModel):
    type: Literal["spec", "diff", "bug_import"]
    label: str
    raw_content: Optional[str] = None
    file_ref: Optional[str] = None


class ArtifactOut(BaseModel):
    id: str
    product_id: str
    type: str
    label: str
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

class DebriefDraft(BaseModel):
    coverage_summary: str
    open_questions: list[str]


# ---- Charters ---------------------------------------------------------

class CharterSuggestRequest(BaseModel):
    artifact_ids: list[str]
    provider: Optional[str] = None  # override the product's default for this call


class CharterCreate(BaseModel):
    mission_statement: str
    rationale: Optional[str] = None
    risk_areas: list[str] = []
    priority: Literal["high", "medium", "low"] = "medium"
    source: Literal["ai-suggested", "risk-based", "follow-up", "manual"] = "manual"
    source_artifact_id: Optional[str] = None
    risk_ids: list[str] = []  # risks this charter investigates (must belong to the product)
    test_ideas: list[str] = []
    oracles: list[str] = []


class CharterOut(BaseModel):
    id: str
    product_id: str
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

    model_config = ConfigDict(from_attributes=True)

    @field_validator("risk_areas", "test_ideas", "oracles", mode="before")
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

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def file_flag(cls, data):
        # The file path stays on the server; the client only needs to know a file exists.
        if hasattr(data, "file_path"):
            return {**{c: getattr(data, c) for c in (
                "id", "session_id", "bug_id", "kind", "summary", "url", "count", "captured_at", "last_seen_at")},
                "has_file": bool(data.file_path)}
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

    model_config = ConfigDict(from_attributes=True)

    @field_validator("steps_to_reproduce", "open_questions", "reproduction_trail", mode="before")
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


# API shapes

class ProductModelRequest(BaseModel):
    artifact_ids: list[str] = Field(min_length=1)
    provider: Optional[str] = None


class ProductModelOut(BaseModel):
    id: str
    product_id: str
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
    product_id: str
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

    model_config = ConfigDict(from_attributes=True)


class ChartersFromRisksRequest(BaseModel):
    risk_ids: Optional[list[str]] = None  # default: every accepted risk
    provider: Optional[str] = None
    max_charters: int = Field(default=6, ge=1, le=20)


class RiskCharterDraft(BaseModel):
    """A charter proposal; save it with POST /products/{id}/charters to use it."""
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
