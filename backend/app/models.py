"""SQLAlchemy models — mirrors the data model in the project scoping doc."""
import uuid
from app.timeutil import utcnow

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Float, JSON, Boolean, Integer, Table
from sqlalchemy.orm import relationship

from app.database import Base
from app.config import settings


def gen_id() -> str:
    return str(uuid.uuid4())


class Project(Base):
    """What a tester works on: one app or product under test. Everything else
    (specs, the product model, risks, charters, and through them sessions and
    bugs) belongs to a project. Called "product" before the projects step."""
    __tablename__ = "projects"
    id = Column(String, primary_key=True, default=gen_id)
    org_id = Column(String, nullable=False, index=True)  # multi-tenancy hook from day one
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    description = Column(Text, nullable=True)  # one line shown on the project card

    default_ai_provider = Column(String, default=lambda: settings.default_ai_provider)
    # What this testing is for, e.g. "Find release blockers in multi-currency checkout".
    # Steers the product model, risk analysis and charters. Added in the risk-model step.
    testing_mission = Column(Text, nullable=True)
    # Where this project's bugs are filed (credentials are in backend/.env)
    github_repo = Column(String, nullable=True)  # "owner/repo"
    jira_project_key = Column(String, nullable=True)  # e.g. "KYC"

    # Where the app under test runs. app_url is the default for the recorded browser,
    # the reproduction agent and regression runs; environments are other named addresses.
    app_url = Column(String, nullable=True)
    environments = Column(JSON, nullable=True)  # [{"name": "staging", "url": "https://..."}]
    default_tester = Column(String, nullable=True)  # prefilled when starting a session

    artifacts = relationship("Artifact", back_populates="project", cascade="all, delete-orphan")
    charters = relationship("Charter", back_populates="project", cascade="all, delete-orphan")
    risks = relationship("Risk", back_populates="project", cascade="all, delete-orphan")
    product_models = relationship("ProductModel", back_populates="project", cascade="all, delete-orphan")


class Artifact(Base):
    """A spec, git diff, or bug-history import that feeds the AI charter/risk suggester."""
    __tablename__ = "artifacts"
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    type = Column(String, nullable=False)  # "spec" | "diff" | "bug_import"
    label = Column(String, nullable=False)  # e.g. "kyc-spec-v3.md", "diff #245"
    raw_content = Column(Text, nullable=True)
    file_ref = Column(String, nullable=True)  # path/URL if it's a file rather than pasted text
    ai_summary = Column(Text, nullable=True)  # cached, regenerated when raw_content changes
    created_at = Column(DateTime, default=utcnow)

    project = relationship("Project", back_populates="artifacts")


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
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    summary = Column(Text, nullable=False)
    elements = Column(JSON, default=list)  # [{"category", "name", "description", "sources"}]
    unknowns = Column(JSON, default=list)  # questions to ask the team
    artifact_ids = Column(JSON, default=list)
    ai_provider = Column(String, nullable=True)  # provenance: who drafted this
    ai_model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    project = relationship("Project", back_populates="product_models")


class Risk(Base):
    """A specific way the product could fail someone who matters.

    AI-proposed risks start as "proposed" and only count once the tester
    accepts them; that review is where the context-driven judgement happens."""
    __tablename__ = "risks"
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    ref = Column(String, nullable=False)  # short per-project label: R1, R2...
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

    project = relationship("Project", back_populates="risks")
    charters = relationship("Charter", secondary=charter_risks, back_populates="risks")
    changes = relationship("RiskChange", back_populates="risk", cascade="all, delete-orphan",
                           order_by="RiskChange.created_at")

    @property
    def score(self) -> int:
        return self.impact * self.likelihood


class Charter(Base):
    __tablename__ = "charters"
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
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

    project = relationship("Project", back_populates="charters")
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

    @property
    def project_id(self) -> str | None:
        """So a session link can find its project's pages."""
        return self.charter.project_id if self.charter else None


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
    # Steps to the bug as the tester edited them (resolved plan steps); None = proposed from the recording
    edited_steps = Column(JSON, nullable=True)

    session = relationship("Session", back_populates="bugs")
    charter = relationship("Charter")
    filings = relationship("BugFiling", back_populates="bug", cascade="all, delete-orphan",
                           order_by="BugFiling.created_at")
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
    kind = Column(String, nullable=False)  # screenshot | trace | har | console_error | page_error | failed_request | navigation | action
    summary = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    file_path = Column(String, nullable=True)  # under settings.evidence_dir
    count = Column(Integer, default=1)
    captured_at = Column(DateTime, default=utcnow)
    last_seen_at = Column(DateTime, default=utcnow)
    data = Column(JSON, nullable=True)  # recorded actions: {"action", "locator", "value", ...}

    session = relationship("Session", back_populates="evidence")
    bug = relationship("Bug", back_populates="evidence")


class RiskChange(Base):
    """One entry in a risk's score history: a suggestion from a session (AI),
    and what the tester decided, or a re-score the tester made by hand.

    Rejected suggestions are kept: a "no" with a reason is information too.
    "no_change" records that a session looked at the risk and found no reason
    to move it, which is also evidence about the risk."""
    __tablename__ = "risk_changes"

    id = Column(String, primary_key=True, default=gen_id)
    risk_id = Column(String, ForeignKey("risks.id"), nullable=False, index=True)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=True, index=True)  # None for manual re-scores
    source = Column(String, nullable=False)  # "ai" | "tester"
    status = Column(String, nullable=False)  # "proposed" | "accepted" | "rejected" | "no_change"
    from_impact = Column(Integer, nullable=False)
    from_likelihood = Column(Integer, nullable=False)
    to_impact = Column(Integer, nullable=False)  # suggested (or, for manual re-scores, chosen)
    to_likelihood = Column(Integer, nullable=False)
    final_impact = Column(Integer, nullable=True)  # what the tester accepted; may differ from the suggestion
    final_likelihood = Column(Integer, nullable=True)
    reason = Column(Text, nullable=True)
    evidence = Column(JSON, default=list)  # quoted note / bug / signal lines
    tester_note = Column(Text, nullable=True)
    ai_provider = Column(String, nullable=True)
    ai_model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    decided_at = Column(DateTime, nullable=True)

    risk = relationship("Risk", back_populates="changes")
    session = relationship("Session")


class BugFiling(Base):
    """A bug sent to an issue tracker: as a new issue, or as a comment adding
    a new occurrence to an existing one. At most one per tracker per bug."""
    __tablename__ = "bug_filings"

    id = Column(String, primary_key=True, default=gen_id)
    bug_id = Column(String, ForeignKey("bugs.id"), nullable=False, index=True)
    tracker = Column(String, nullable=False)  # "github" | "jira"
    target = Column(String, nullable=False)  # repo or project key at filing time
    mode = Column(String, nullable=False)  # "new" | "comment"
    issue_key = Column(String, nullable=False)  # "#42" or "KYC-42"
    issue_url = Column(String, nullable=False)
    attachments = Column(JSON, default=list)  # file names attached in the tracker
    created_at = Column(DateTime, default=utcnow)

    bug = relationship("Bug", back_populates="filings")


class RegressionTest(Base):
    """A Playwright regression test for a reviewed bug. The AI chooses the
    recorded steps and the assertions (the plan); Mirmera renders the code from
    the plan and can replay it against the app to check it reproduces the bug.
    Regenerating adds a new row; the latest one is shown."""
    __tablename__ = "regression_tests"

    id = Column(String, primary_key=True, default=gen_id)
    bug_id = Column(String, ForeignKey("bugs.id"), nullable=False, index=True)
    plan = Column(JSON, nullable=False)  # resolved: steps carry the recorded actions themselves
    code = Column(Text, nullable=False)
    filename = Column(String, nullable=False)
    base_url = Column(String, nullable=True)
    known_failing = Column(Boolean, default=True)
    warnings = Column(JSON, default=list)
    ai_provider = Column(String, nullable=True)
    ai_model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    replay = Column(JSON, nullable=True)
    replayed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class ReproductionRun(Base):
    """One attempt by the reproduction agent to reproduce a bug in a browser.
    Steps are saved as they happen, so the tester can follow along."""
    __tablename__ = "reproduction_runs"

    id = Column(String, primary_key=True, default=gen_id)
    bug_id = Column(String, ForeignKey("bugs.id"), nullable=False, index=True)
    status = Column(String, default="running")  # running | reproduced | not_reproduced | cancelled | error
    base_url = Column(String, nullable=False)
    start_url = Column(String, nullable=False)
    max_steps = Column(Integer, nullable=False)
    runs = Column(Integer, nullable=False)
    steps = Column(JSON, default=list)  # [{"n", "thought", "action", "status", "detail", "url"}]
    symptom = Column(JSON, nullable=True)  # verified symptom
    detail = Column(Text, nullable=True)  # why it stopped
    reproducibility = Column(JSON, nullable=True)
    plan = Column(JSON, nullable=True)  # the found steps, as a resolved regression plan
    screenshot_path = Column(String, nullable=True)
    cancel_requested = Column(Boolean, default=False)
    ai_provider = Column(String, nullable=True)
    ai_model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
