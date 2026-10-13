"""SQLAlchemy models — mirrors the data model in the project scoping doc."""
import uuid
from app.timeutil import utcnow

from sqlalchemy import Column, Date, String, DateTime, ForeignKey, Text, Float, JSON, Boolean, Integer, Table, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base
from app.config import settings


def gen_id() -> str:
    return str(uuid.uuid4())


class User(Base):
    """Someone who signs in. Belongs to workspaces through memberships."""
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_id)
    email = Column(String, nullable=False, unique=True, index=True)  # stored lower-case
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)  # scrypt, see app/auth.py
    created_at = Column(DateTime, default=utcnow)
    last_login_at = Column(DateTime, nullable=True)

    memberships = relationship("Membership", back_populates="user", cascade="all, delete-orphan")


class Workspace(Base):
    """A team or company. Its projects, and everything in them, are visible only to its members.
    Projects keep the workspace id in their org_id column."""
    __tablename__ = "workspaces"
    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    memberships = relationship("Membership", back_populates="workspace", cascade="all, delete-orphan")


class Membership(Base):
    """A user in a workspace, with a role: owner | admin | tester | viewer."""
    __tablename__ = "memberships"
    id = Column(String, primary_key=True, default=gen_id)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String, nullable=False, default="tester")
    created_at = Column(DateTime, default=utcnow)

    workspace = relationship("Workspace", back_populates="memberships")
    user = relationship("User", back_populates="memberships")


class WorkspaceKey(Base):
    """A workspace's own key for GitHub, Jira, an AI provider, Telegram or Slack,
    encrypted with the server key (see app/keys.py)."""
    __tablename__ = "workspace_keys"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id = Column(String, primary_key=True, default=gen_id)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False, index=True)
    name = Column(String, nullable=False)  # one of app.keys.NAMES
    value = Column(Text, nullable=False)  # Fernet token, never the plain value
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    updater = relationship("User")


class Invitation(Base):
    """An invitation to join a workspace with a role. The link holds a random token; only
    its hash is stored. Accepting needs an account with the invited email."""
    __tablename__ = "invitations"
    id = Column(String, primary_key=True, default=gen_id)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False, index=True)
    email = Column(String, nullable=False)  # lower-case
    role = Column(String, nullable=False, default="tester")
    token_hash = Column(String, nullable=False, unique=True, index=True)
    invited_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)

    workspace = relationship("Workspace")
    inviter = relationship("User")


class PasswordReset(Base):
    """A one-time link to choose a new password; valid for an hour, deleted once used."""
    __tablename__ = "password_resets"
    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=False)

    user = relationship("User")


class AuthToken(Base):
    """A signed-in browser (kind "session", sent as a cookie) or an API token for the
    Chrome extension (kind "api", sent as a Bearer header). Only a hash is stored."""
    __tablename__ = "auth_tokens"
    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)  # sha256 of the token
    kind = Column(String, nullable=False, default="session")  # "session" | "api"
    label = Column(String, nullable=True)  # for API tokens: "Chrome on my laptop"
    created_at = Column(DateTime, default=utcnow)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)  # None: until revoked (API tokens)

    user = relationship("User")


class Project(Base):
    """What a tester works on: one app or product under test. Everything else
    (specs, the product model, risks, charters, and through them sessions and
    bugs) belongs to a project. Called "product" before the projects step."""
    __tablename__ = "projects"
    id = Column(String, primary_key=True, default=gen_id)
    org_id = Column(String, nullable=False, index=True)  # the workspace it belongs to (Workspace.id)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    description = Column(Text, nullable=True)  # one line shown on the project card
    archived_at = Column(DateTime, nullable=True)  # hidden from the Projects page until unarchived

    default_ai_provider = Column(String, default=lambda: settings.default_ai_provider)
    # Per-task choices, e.g. {"debrief": "local", "regression": "claude"}; others use the default
    ai_task_providers = Column(JSON, nullable=True)
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
    # How to sign in to the app under test (app/sign_in.py); the password is encrypted
    sign_in = Column(JSON, nullable=True)
    default_tester = Column(String, nullable=True)  # prefilled when starting a session
    ethiopia = Column(Boolean, default=False)  # the app's users are in Ethiopia: the Ethiopia pack's ideas and check
    notify = Column(JSON, nullable=True)  # {"telegram_chat_id", "slack": bool, "events": [...]}

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


# Which stories a risk or charter belongs to (many-to-many). A story links to
# risks and charters; it doesn't own them, so a risk outlives its story.
story_risks = Table(
    "story_risks",
    Base.metadata,
    Column("story_id", String, ForeignKey("stories.id"), primary_key=True),
    Column("risk_id", String, ForeignKey("risks.id"), primary_key=True),
)
story_charters = Table(
    "story_charters",
    Base.metadata,
    Column("story_id", String, ForeignKey("stories.id"), primary_key=True),
    Column("charter_id", String, ForeignKey("charters.id"), primary_key=True),
)


class Story(Base):
    """A user story (or any change the team plans): its claims are one oracle among
    several. Risks and charters are linked to it, not kept inside it."""
    __tablename__ = "stories"
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    key = Column(String, nullable=True)  # "KYC-12", "#45"; None for one written here
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)  # what it claims, acceptance criteria included
    url = Column(String, nullable=True)
    tracker = Column(String, nullable=True)  # "github" | "jira" | None
    tracker_status = Column(String, nullable=True)  # as the tracker says it, e.g. "In Progress"
    closed = Column(Boolean, default=False)
    artifact_id = Column(String, ForeignKey("artifacts.id"), nullable=True)  # made for charter suggestions
    sprint_id = Column(String, ForeignKey("sprints.id"), nullable=True)  # one sprint at a time; moved when carried over
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    risks = relationship("Risk", secondary=story_risks, back_populates="stories")
    charters = relationship("Charter", secondary=story_charters, back_populates="stories")
    sprint = relationship("Sprint", back_populates="stories")

    @property
    def ref(self) -> dict:
        return {"id": self.id, "key": self.key, "title": self.title}


class Sprint(Base):
    """A time box with the stories planned for it. Its testing is everything done in
    those dates, under its stories or not."""
    __tablename__ = "sprints"
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String, nullable=False)  # "Sprint 14"
    goal = Column(Text, nullable=True)
    starts_on = Column(Date, nullable=False)
    ends_on = Column(Date, nullable=False)  # included
    jira_id = Column(String, nullable=True)  # the Jira sprint it came from, when a story import made or matched it
    created_at = Column(DateTime, default=utcnow)

    stories = relationship("Story", back_populates="sprint")


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
    stories = relationship("Story", secondary="story_risks", back_populates="risks")
    changes = relationship("RiskChange", back_populates="risk", cascade="all, delete-orphan",
                           order_by="RiskChange.created_at")

    @property
    def score(self) -> int:
        return self.impact * self.likelihood

    @property
    def story_refs(self) -> list[dict]:
        return [st.ref for st in self.stories]


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
    heuristics = Column(JSON, nullable=True)  # keys from the heuristics library, chosen by the tester

    project = relationship("Project", back_populates="charters")
    sessions = relationship("Session", back_populates="charter")
    risks = relationship("Risk", secondary=charter_risks, back_populates="charters")
    source_artifact = relationship("Artifact")
    stories = relationship("Story", secondary="story_charters", back_populates="charters")

    @property
    def risk_ids(self) -> list[str]:
        return [r.id for r in self.risks]

    @property
    def story_refs(self) -> list[dict]:
        return [st.ref for st in self.stories]

    @property
    def source_label(self) -> str | None:
        """The change a suggested charter came from: "PR #42: …", "git diff main...x"."""
        return self.source_artifact.label if self.source_artifact else None

    @property
    def source_url(self) -> str | None:
        ref = self.source_artifact.file_ref if self.source_artifact else None
        return ref if ref and ref.startswith(("http://", "https://")) else None


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
    dismissed_nudges = Column(JSON, nullable=True)  # test-idea nudge ids hidden for this session

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
    # What the exploration agent saw, as a key: the same problem met again (another charter, another
    # run) is added to this bug as a sighting instead of becoming a second draft.
    signature = Column(String, nullable=True, index=True)
    sightings = Column(JSON, nullable=True)  # [{"session_id", "charter_id", "mission", "at", "url"}]
    symptom = Column(JSON, nullable=True)  # what shows it, as the agent's checks see it: {"kind", "value", "explanation"}
    confirmation = Column(JSON, nullable=True)  # its steps replayed in fresh browsers: {"runs", "reproduced", "results", "at"}
    dismissal = Column(JSON, nullable=True)  # a tester said it isn't a bug: {"reason", "note", "by", "at"}

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
    drafting = Column(Boolean, default=False)  # the AI is still writing it; the session has already ended

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
    # What "Check closed issues" saw last: a retest runs when this goes from open to closed.
    issue_closed = Column(Boolean, nullable=True)  # None: never checked (counts as open)
    issue_status = Column(String, nullable=True)  # the tracker's own word: "closed", "Done"...
    status_checked_at = Column(DateTime, nullable=True)

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


class IsolationRun(Base):
    """Replays a bug's steps with one recorded value changed at a time, to find
    which ones the bug depends on. Variants are saved as they finish."""
    __tablename__ = "isolation_runs"

    id = Column(String, primary_key=True, default=gen_id)
    bug_id = Column(String, ForeignKey("bugs.id"), nullable=False, index=True)
    status = Column(String, default="running")  # running | done | not_reproduced | cancelled | error
    base_url = Column(String, nullable=False)
    symptom = Column(JSON, nullable=False)  # {"kind": "failed_request" | "text", "value"}
    baseline = Column(JSON, nullable=True)  # {"result", "detail"}
    variants = Column(JSON, default=list)  # [{"n", "step", "field", "change", "result", "detail"}]
    total = Column(Integer, default=0)  # variants planned
    summary = Column(JSON, nullable=True)  # {"depends_on": [...], "irrelevant": [...], "inconclusive": [...]}
    detail = Column(Text, nullable=True)
    cancel_requested = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)


class RegressionSuiteRun(Base):
    """Replays the latest regression test of every bug in a project, one after
    another, against one address. Results are saved as each test finishes."""
    __tablename__ = "regression_suite_runs"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    status = Column(String, default="running")  # running | done | cancelled | error
    base_url = Column(String, nullable=True)  # None: each test's own recorded address
    browser = Column(String, nullable=True)  # None: each test in the browser its bug was found in
    total = Column(Integer, default=0)
    results = Column(JSON, default=list)  # [{"bug_id", "bug_title", "test_id", "known_failing", "outcome", "duration_ms", "detail"}]
    detail = Column(Text, nullable=True)
    cancel_requested = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)


class Retest(Base):
    """A bug's regression test replayed because its issue was closed, and the
    comment Mirmera left on the issue with the result. One per close: an issue
    reopened and closed again is retested again."""
    __tablename__ = "retests"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    bug_id = Column(String, ForeignKey("bugs.id"), nullable=False, index=True)
    filing_id = Column(String, ForeignKey("bug_filings.id"), nullable=False, index=True)
    tracker = Column(String, nullable=False)
    issue_key = Column(String, nullable=False)
    issue_url = Column(String, nullable=False)
    test_id = Column(String, nullable=True)  # None: the bug had no regression test
    # running | fixed | still_fails | could_not_check | no_test
    outcome = Column(String, default="running")
    replay = Column(JSON, nullable=True)  # the replay result, as for a regression test
    detail = Column(Text, nullable=True)  # what went wrong, in words
    commented = Column(Boolean, default=False)
    comment_error = Column(Text, nullable=True)  # why the comment could not be posted
    created_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)


class ReportSummary(Base):
    """An AI-drafted summary the tester kept (maybe edited) for one report period.
    since/until are "" for an open end, so a period is one exact key."""
    __tablename__ = "report_summaries"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    since = Column(String, default="")
    until = Column(String, default="")
    text = Column(Text, nullable=False)
    edited = Column(Boolean, default=False)
    ai_provider = Column(String, nullable=True)
    ai_model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class NotificationLog(Base):
    """One message to one channel: what was sent, and whether it arrived."""
    __tablename__ = "notification_logs"

    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    event = Column(String, nullable=False)  # fix_failed | fix_verified | suite_problem | high_bug | test
    channel = Column(String, nullable=False)  # telegram | slack
    text = Column(Text, nullable=False)
    status = Column(String, nullable=False)  # sent | failed | mock
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class TestStrategy(Base):
    """One saved version of a project's test strategy (Heuristic Test Strategy Model sections),
    for the whole project or focused on a sprint. Saving makes a new version; old ones stay."""
    __tablename__ = "test_strategies"
    __test__ = False  # not a pytest test class, despite the name
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    sprint_id = Column(String, ForeignKey("sprints.id"), nullable=True, index=True)  # None: the whole project
    version = Column(Integer, nullable=False)
    sections = Column(JSON, nullable=False)  # {"mission": "...", "context": "...", ...}
    ai_provider = Column(String, nullable=True)  # set when an AI drafted the prose it started from
    ai_model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    sprint = relationship("Sprint")
    author = relationship("User")


class JiraLinkCode(Base):
    """A one-time code a workspace admin makes in Mirmera and a Jira admin pastes into the
    Mirmera app in Jira, to link that Jira site to the workspace. Only its hash is stored."""
    __tablename__ = "jira_link_codes"
    id = Column(String, primary_key=True, default=gen_id)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False, index=True)
    code_hash = Column(String, nullable=False, unique=True, index=True)  # sha256 of the code
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=False)


class JiraInstallation(Base):
    """The Mirmera app installed on a Jira site (a Forge installation), linked to one workspace.
    Requests from the app carry a token signed by Atlassian that names the installation."""
    __tablename__ = "jira_installations"
    id = Column(String, primary_key=True, default=gen_id)
    installation_id = Column(String, nullable=False, unique=True, index=True)  # from the Forge invocation token
    cloud_id = Column(String, nullable=True)  # the Jira site
    site_url = Column(String, nullable=True)  # "https://acme.atlassian.net", as the app reports it
    environment = Column(String, nullable=True)  # Forge environment: DEVELOPMENT | STAGING | PRODUCTION
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False, index=True)
    linked_by = Column(String, ForeignKey("users.id"), nullable=True)  # who made the code
    linked_by_jira = Column(String, nullable=True)  # Atlassian account id that pasted it
    linked_at = Column(DateTime, default=utcnow)
    last_seen_at = Column(DateTime, nullable=True)

    workspace = relationship("Workspace")
    linker = relationship("User")


class JiraAgentTask(Base):
    """One piece of work Jira gave the Mirmera agent: an issue assigned to it, or a mention.
    Jira polls it (A2A GetTask) until it is completed, failed, rejected or canceled."""
    __tablename__ = "jira_agent_tasks"
    id = Column(String, primary_key=True, default=gen_id)  # the A2A task id
    context_id = Column(String, nullable=False, index=True)  # the A2A conversation; follow-ups reuse it
    installation_id = Column(String, nullable=False, index=True)  # Forge installation that sent it
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=True)
    invocation = Column(String, nullable=True)  # ISSUE_ASSIGNMENT | ISSUE_COMMENT_MENTION | ...
    issue_key = Column(String, nullable=True)
    issue_title = Column(String, nullable=True)
    issue_text = Column(Text, nullable=True)  # the issue's description, as Jira sent it
    request = Column(Text, nullable=True)  # a mention's comment or a chat message
    jira_account = Column(String, nullable=True)  # who assigned or mentioned
    action = Column(String, nullable=True)  # plan | status
    state = Column(String, nullable=False, default="TASK_STATE_SUBMITTED")
    message = Column(Text, nullable=True)  # what Jira shows; the user can draft a comment from it
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    story_id = Column(String, ForeignKey("stories.id"), nullable=True)
    # Where the work is, for the progress shown in Mirmera: read | model | risks | charters | explore | done
    stage = Column(String, nullable=True)
    exploration_run_id = Column(String, ForeignKey("exploration_runs.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class AiJob(Base):
    """A step where the AI works for a while on a tester's request (build the product model,
    propose risks, draft charters from risks). It runs in the background and its result is
    kept, so a refresh, another tab or a colleague sees it working and then sees the result."""
    __tablename__ = "ai_jobs"
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    kind = Column(String, nullable=False)  # product_model | risk_analysis | charters_from_risks
    status = Column(String, default="running")  # running | done | failed
    request = Column(JSON, default=dict)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    started_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)


class ExplorationRun(Base):
    """The exploration agent working through one charter: an AI drives a headless browser
    on the app, records a session (notes, actions, signals) and drafts bugs for a tester to
    review. Nothing it finds is filed anywhere until a tester reviews it."""
    __tablename__ = "exploration_runs"
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    charter_id = Column(String, ForeignKey("charters.id"), nullable=False, index=True)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=True)
    status = Column(String, default="running")  # running | finished | cancelled | error
    start_url = Column(String, nullable=False)
    max_steps = Column(Integer, default=25)
    steps = Column(JSON, default=list)  # [{"n", "thought", "action", "kind", "status", "detail", "url"}]
    bug_ids = Column(JSON, default=list)
    summary = Column(Text, nullable=True)  # the agent's own wrap-up
    detail = Column(Text, nullable=True)  # why it stopped
    started_by = Column(String, nullable=True)  # a user's name, or "Jira KYC-12"
    cancel_requested = Column(Boolean, default=False)
    video = Column(String, nullable=True)  # its browser's recording (WebM), in the session's folder
    conditions = Column(JSON, nullable=True)  # {"device": "phone", "network": "slow", "locale", "timezone"}: how it browsed
    hints = Column(JSON, nullable=True)  # [{"id", "text", "by", "at", "step"}]: what a tester (or Mirmera) asked it to try next
    journey = Column(JSON, nullable=True)  # its steps as a replay does them ({"type": "goto" | "action", ...}): to compare environments
    ai_provider = Column(String, nullable=True)
    ai_model = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)

    charter = relationship("Charter")
    session = relationship("Session")


class Comparison(Base):
    """A session's journey done again on two versions or environments, side by side, step by step,
    with what differed. Nothing is judged by AI: a difference is something both browsers saw."""
    __tablename__ = "comparisons"
    id = Column(String, primary_key=True, default=gen_id)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False, index=True)
    env_a = Column(JSON, nullable=False)  # {"name", "url"}
    env_b = Column(JSON, nullable=False)
    steps = Column(JSON, default=list)  # [{"n", "description", "a", "b", "differences", "shots"}]
    journey = Column(JSON, default=list)  # the steps done, as a replay does them
    summary = Column(Text, nullable=True)
    status = Column(String, default="done")  # done | error
    detail = Column(Text, nullable=True)
    started_by = Column(String, nullable=True)
    bug_ids = Column(JSON, default=list)  # drafts made from its differences
    created_at = Column(DateTime, default=utcnow)


class Dismissal(Base):
    """A draft a tester dismissed, and why: Mirmera doesn't draft that problem again in the project
    (see app/dismissals.py)."""
    __tablename__ = "dismissals"
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    bug_id = Column(String, nullable=True)  # the draft it came from (kept even if that bug is deleted)
    fingerprint = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    reason = Column(String, nullable=False)  # by_design | not_a_problem | not_important | duplicate | cant_reproduce
    note = Column(Text, nullable=True)
    by = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class TestData(Base):
    """A value the team keeps for the app under test, and which fields it fills (see app/test_data.py)."""
    __tablename__ = "test_data"
    __test__ = False  # not a pytest class
    id = Column(String, primary_key=True, default=gen_id)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String, nullable=False)  # "valid national ID": how people, steps ({data:…}) and the agent name it
    value = Column(Text, nullable=False)  # encrypted when secret
    secret = Column(Boolean, default=False)
    labels = Column(JSON, default=list)  # the app's field labels it fills: ["National ID", "ID number"]
    page = Column(String, nullable=True)  # only on this page (a path), when a label means different things
    kind = Column(String, default="valid")  # valid | invalid | edge
    once = Column(Boolean, default=False)  # works once (a voucher): marked used when the app accepts it
    used_at = Column(DateTime, nullable=True)
    used_by_run = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)  # a run met one of its fields
    missed_at = Column(DateTime, nullable=True)  # a run saw forms but none of its fields: renamed?
    created_at = Column(DateTime, default=utcnow)
