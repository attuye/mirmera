"""SQLAlchemy models — mirrors the data model in the project scoping doc."""
import uuid
from app.timeutil import utcnow

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Float, JSON, Boolean, Integer, Table
from sqlalchemy.orm import relationship

from app.database import Base
from app.config import settings


def gen_id() -> str:
    return str(uuid.uuid4())


class Product(Base):
    __tablename__ = "products"
    id = Column(String, primary_key=True, default=gen_id)
    org_id = Column(String, nullable=False, index=True)  # multi-tenancy hook from day one
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    default_ai_provider = Column(String, default=lambda: settings.default_ai_provider)
    # What this testing is for, e.g. "Find release blockers in multi-currency checkout".
    # Steers the product model, risk analysis and charters. Added in the risk-model step.
    testing_mission = Column(Text, nullable=True)

    artifacts = relationship("Artifact", back_populates="product", cascade="all, delete-orphan")
    charters = relationship("Charter", back_populates="product", cascade="all, delete-orphan")
    risks = relationship("Risk", back_populates="product", cascade="all, delete-orphan")
    product_models = relationship("ProductModel", back_populates="product", cascade="all, delete-orphan")


class Artifact(Base):
    """A spec, git diff, or bug-history import that feeds the AI charter/risk suggester."""
    __tablename__ = "artifacts"
    id = Column(String, primary_key=True, default=gen_id)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    type = Column(String, nullable=False)  # "spec" | "diff" | "bug_import"
    label = Column(String, nullable=False)  # e.g. "kyc-spec-v3.md", "diff #245"
    raw_content = Column(Text, nullable=True)
    file_ref = Column(String, nullable=True)  # path/URL if it's a file rather than pasted text
    ai_summary = Column(Text, nullable=True)  # cached, regenerated when raw_content changes
    created_at = Column(DateTime, default=utcnow)

    product = relationship("Product", back_populates="artifacts")


# Which risks a charter investigates (many-to-many).
charter_risks = Table(
    "charter_risks",
    Base.metadata,
    Column("charter_id", String, ForeignKey("charters.id"), primary_key=True),
    Column("risk_id", String, ForeignKey("risks.id"), primary_key=True),
)


class ProductModel(Base):
    """An SFDIPOT model of the product (Heuristic Test Strategy Model), built from
    artifacts. Risk analysis starts from the latest one. Kept as snapshots so you
    can see how your understanding of the product changed over time."""
    __tablename__ = "product_models"
    id = Column(String, primary_key=True, default=gen_id)
    product_id = Column(String, ForeignKey("products.id"), nullable=False, index=True)
    summary = Column(Text, nullable=False)
    elements = Column(JSON, default=list)  # [{"category", "name", "description", "sources"}]
    unknowns = Column(JSON, default=list)  # questions to ask the team
    artifact_ids = Column(JSON, default=list)
    ai_provider = Column(String, nullable=True)  # provenance: who drafted this
    ai_model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    product = relationship("Product", back_populates="product_models")


class Risk(Base):
    """A specific way the product could fail someone who matters.

    AI-proposed risks start as "proposed" and only count once the tester
    accepts them; that review is where the context-driven judgement happens."""
    __tablename__ = "risks"
    id = Column(String, primary_key=True, default=gen_id)
    product_id = Column(String, ForeignKey("products.id"), nullable=False, index=True)
    ref = Column(String, nullable=False)  # short per-product label: R1, R2...
    title = Column(String, nullable=False)
    failure_mode = Column(Text, nullable=False)  # what fails, for whom, when
    quality_criterion = Column(String, nullable=False)  # capability, security, usability...
    elements = Column(JSON, default=list)  # product-model elements involved
    impact = Column(Integer, nullable=False)  # 1-5
    likelihood = Column(Integer, nullable=False)  # 1-5
    rationale = Column(Text, nullable=True)
    evidence = Column(String, nullable=False)  # "stated" | "inferred" | "speculative" | "tester"
    status = Column(String, default="proposed")  # "proposed" | "accepted" | "retired"
    source = Column(String, default="ai")  # "ai" | "manual" | "session" (promoted from a debrief)
    tester_notes = Column(Text, nullable=True)
    ai_provider = Column(String, nullable=True)
    ai_model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    last_change = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    product = relationship("Product", back_populates="risks")
    charters = relationship("Charter", secondary=charter_risks, back_populates="risks")

    @property
    def score(self) -> int:
        return self.impact * self.likelihood


class Charter(Base):
    __tablename__ = "charters"
    id = Column(String, primary_key=True, default=gen_id)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    mission_statement = Column(Text, nullable=False)
    rationale = Column(Text, nullable=True)
    risk_areas = Column(JSON, default=list)  # list[str]
    priority = Column(String, default="medium")  # "high" | "medium" | "low"
    source = Column(String, default="manual")  # "ai-suggested" | "manual"
    source_artifact_id = Column(String, ForeignKey("artifacts.id"), nullable=True)
    status = Column(String, default="proposed")  # "proposed" | "active" | "done"
    created_at = Column(DateTime, default=utcnow)
    # Added in the risk-model step:
    test_ideas = Column(JSON, default=list)  # starting ideas, not scripted steps
    oracles = Column(JSON, default=list)  # FEW HICCUPPS heuristics to spot problems

    product = relationship("Product", back_populates="charters")
    sessions = relationship("Session", back_populates="charter")
    risks = relationship("Risk", secondary=charter_risks, back_populates="charters")

    @property
    def risk_ids(self) -> list[str]:
        return [r.id for r in self.risks]


class Session(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True, default=gen_id)
    charter_id = Column(String, ForeignKey("charters.id"), nullable=False)
    tester_id = Column(String, nullable=False)
    started_at = Column(DateTime, default=utcnow)
    ended_at = Column(DateTime, nullable=True)
    paused_at = Column(DateTime, nullable=True)  # set while paused, null while running
    total_paused_seconds = Column(Float, default=0.0)  # accumulated across all pause/resume cycles
    duration_target_minutes = Column(Float, default=90)

    charter = relationship("Charter", back_populates="sessions")
    notes = relationship("Note", back_populates="session", cascade="all, delete-orphan")
    bugs = relationship("Bug", back_populates="session", cascade="all, delete-orphan")
    debrief = relationship("Debrief", back_populates="session", uselist=False, cascade="all, delete-orphan")
    evidence = relationship("Evidence", back_populates="session", cascade="all, delete-orphan",
                            order_by="Evidence.captured_at")


class Note(Base):
    __tablename__ = "notes"
    id = Column(String, primary_key=True, default=gen_id)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    tag = Column(String, nullable=False)  # "test" | "bug" | "issue" | "question" | "setup"
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=utcnow)

    session = relationship("Session", back_populates="notes")


class Bug(Base):
    __tablename__ = "bugs"
    id = Column(String, primary_key=True, default=gen_id)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    charter_id = Column(String, ForeignKey("charters.id"), nullable=False)  # denormalized for the evidence trail
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    reproduction_trail = Column(JSON, default=list)  # snapshot of preceding notes at log time
    environment = Column(JSON, default=dict)  # {"env", "browser", "user", ...}
    attachments = Column(JSON, default=list)  # list of file refs
    severity = Column(String, default="medium")  # "high" | "medium" | "low"
    status = Column(String, default="new")
    created_at = Column(DateTime, default=utcnow)

    # The bug report, added in the bug-report step. Quick-logged bugs leave these
    # empty until the tester reviews them. kind is None ("unreviewed") until then;
    # a "bug" must name the oracle that makes it a problem, otherwise it's a "question".
    kind = Column(String, nullable=True)  # None | "bug" | "question"
    steps_to_reproduce = Column(JSON, nullable=True)  # list[str]
    expected = Column(Text, nullable=True)
    actual = Column(Text, nullable=True)
    oracle_heuristic = Column(String, nullable=True)  # FEW HICCUPPS, e.g. "claims"
    oracle_explanation = Column(Text, nullable=True)
    severity_rationale = Column(Text, nullable=True)
    reproducibility = Column(String, nullable=True)  # "always" | "intermittent" | "once" | "unknown"
    open_questions = Column(JSON, nullable=True)  # list[str]
    # The AI's latest draft, kept unchanged next to the tester's version (like Debrief.ai_draft).
    ai_draft = Column(JSON, nullable=True)
    ai_provider = Column(String, nullable=True)
    ai_model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    last_change = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    session = relationship("Session", back_populates="bugs")
    charter = relationship("Charter")
    evidence = relationship("Evidence", back_populates="bug", order_by="Evidence.captured_at")


class Debrief(Base):
    __tablename__ = "debriefs"
    id = Column(String, primary_key=True, default=gen_id)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False, unique=True)
    coverage_summary = Column(Text, nullable=True)
    time_breakdown = Column(JSON, default=dict)  # {"on_charter": .66, "opportunity": .18, ...}
    open_questions = Column(JSON, default=list)
    ai_draft = Column(Text, nullable=True)  # raw AI-generated draft, kept for comparison
    final_text = Column(Text, nullable=True)  # tester-edited version actually saved
    created_at = Column(DateTime, default=utcnow)

    # PROOF report (Past, Results, Obstacles, Outlook, Feelings), added in the debrief step.
    # The AI drafts Past/Results/Obstacles/Outlook; Feelings only ever comes from the tester.
    # coverage_summary above holds the Past narrative, so older screens keep working.
    charter_status = Column(String, nullable=True)  # "fulfilled" | "partially_fulfilled" | "not_fulfilled"
    charter_status_rationale = Column(Text, nullable=True)
    results = Column(JSON, nullable=True)  # list[str]
    obstacles = Column(JSON, nullable=True)  # list[str]
    outlook = Column(JSON, nullable=True)  # [{"charter", "reason", "charter_id"}]; charter_id once saved
    risks_observed = Column(JSON, nullable=True)  # [{"text", "risk_id"}]; risk_id once promoted
    tester_feelings = Column(Text, nullable=True)
    ai_provider = Column(String, nullable=True)
    ai_model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)

    session = relationship("Session", back_populates="debrief")


class Evidence(Base):
    """Something the session runner recorded: a file (screenshot, trace, HAR)
    or a signal (console error, page error, failed request, page visited).

    Repeats of the same signal are counted on one row instead of one row each,
    so a page that logs an error every second stays readable."""
    __tablename__ = "evidence"

    id = Column(String, primary_key=True, default=gen_id)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False, index=True)
    bug_id = Column(String, ForeignKey("bugs.id"), nullable=True, index=True)  # screenshot taken when the bug was logged
    kind = Column(String, nullable=False)  # screenshot | trace | har | console_error | page_error | failed_request | navigation
    summary = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    file_path = Column(String, nullable=True)  # under settings.evidence_dir
    count = Column(Integer, default=1)
    captured_at = Column(DateTime, default=utcnow)
    last_seen_at = Column(DateTime, default=utcnow)

    session = relationship("Session", back_populates="evidence")
    bug = relationship("Bug", back_populates="evidence")
