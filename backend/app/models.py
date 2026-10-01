"""SQLAlchemy models — mirrors the data model in the project scoping doc."""
import uuid
from app.timeutil import utcnow

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Float, JSON, Boolean
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

    artifacts = relationship("Artifact", back_populates="product", cascade="all, delete-orphan")
    charters = relationship("Charter", back_populates="product", cascade="all, delete-orphan")


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

    product = relationship("Product", back_populates="charters")
    sessions = relationship("Session", back_populates="charter")


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

    session = relationship("Session", back_populates="bugs")


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

    session = relationship("Session", back_populates="debrief")
