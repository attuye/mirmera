"""Pydantic schemas — request/response shapes, and the AI provider's structured output contracts."""
import logging
import re
from datetime import datetime
from typing import ClassVar, Optional, Literal

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
    org_id: str = "local"
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
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("environments", mode="before")
    @classmethod
    def _none_is_empty(cls, v):
        return v or []


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


class ArtifactOut(BaseModel):
    id: str
    project_id: str
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
    # Where it was filed: GitHub and/or Jira
    filings: list["BugFilingOut"] = []

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

    model_config = ConfigDict(from_attributes=True)


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
    verdict: Literal["likely_duplicate", "related", "different", "not_assessed"]
    reason: str


class DuplicateCheckOut(BaseModel):
    tracker: str
    target: str
    searched_for: list[str]  # the words used, so the tester can judge the search
    candidates: list[DuplicateCandidate]
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
    kind: Literal["failed_request", "console_error", "page_error", "text_visible", "text_absent", "url"]
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
