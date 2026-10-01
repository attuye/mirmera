"""Pydantic schemas — request/response shapes, and the AI provider's structured output contracts."""
from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    source: Literal["ai-suggested", "risk-based", "manual"] = "manual"
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

    model_config = ConfigDict(from_attributes=True)

class BugListItem(BaseModel):
    id: str
    session_id: str
    charter_mission: str
    title: str
    severity: str
    status: str
    reproduction_trail: list
    created_at: datetime

# ---- Debrief ------------------------------------------------------------

class DebriefOut(BaseModel):
    id: str
    session_id: str
    coverage_summary: Optional[str]
    time_breakdown: dict
    open_questions: list
    ai_draft: Optional[str]
    final_text: Optional[str]

    model_config = ConfigDict(from_attributes=True)

class DebriefListItem(BaseModel):
    id: str
    session_id: str
    charter_mission: str
    ended_at: Optional[datetime]
    coverage_summary: Optional[str]
    created_at: datetime
    
class DebriefUpdate(BaseModel):
    final_text: str


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
