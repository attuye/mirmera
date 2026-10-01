import logging
from app.timeutil import utcnow

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai import tasks
from app.ai.base import ProviderError, get_provider
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])
bugs_list_router = APIRouter(tags=["bugs"])

TAG_PHASE = {
    "test": "on_charter",
    "issue": "opportunity",
    "question": "opportunity",
    "bug": "bug_investigation",
    "setup": "setup",
}


def _compute_time_breakdown(notes: list[models.Note], ended_at) -> dict[str, float]:
    """Buckets elapsed session time into SBTM phases based on each note's tag,
    using the gap to the next note (or session end) as that note's duration.
    Rough by nature — attention doesn't snap cleanly at note boundaries — but
    gives a directionally useful split for the debrief."""
    if not notes:
        return {}

    totals = {"on_charter": 0.0, "opportunity": 0.0, "bug_investigation": 0.0, "setup": 0.0}
    boundaries = [n.timestamp for n in notes] + [ended_at]
    for note, next_ts in zip(notes, boundaries[1:]):
        duration = (next_ts - note.timestamp).total_seconds()
        totals[TAG_PHASE.get(note.tag, "on_charter")] += max(duration, 0)

    total_time = sum(totals.values())
    if total_time == 0:
        return {}
    return {k: round(v / total_time, 3) for k, v in totals.items()}

@router.post("", response_model=schemas.SessionOut)
def start_session(payload: schemas.SessionCreate, db: DBSession = Depends(get_db)):
    charter = db.get(models.Charter, payload.charter_id)
    if not charter:
        raise HTTPException(404, "Charter not found")

    session = models.Session(**payload.model_dump())
    charter.status = "active"
    db.add(session)
    db.commit()
    db.refresh(session)
    return session

@router.get("/{session_id}", response_model=schemas.SessionOut)
def get_session(session_id: str, db: DBSession = Depends(get_db)):
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session

@router.post("/{session_id}/pause", response_model=schemas.SessionOut)
def pause_session(session_id: str, db: DBSession = Depends(get_db)):
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.ended_at:
        raise HTTPException(400, "Session already ended")
    if session.paused_at:
        raise HTTPException(400, "Session already paused")
    session.paused_at = utcnow()
    db.commit()
    db.refresh(session)
    return session


@router.post("/{session_id}/resume", response_model=schemas.SessionOut)
def resume_session(session_id: str, db: DBSession = Depends(get_db)):
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if not session.paused_at:
        raise HTTPException(400, "Session is not paused")
    session.total_paused_seconds += (utcnow() - session.paused_at).total_seconds()
    session.paused_at = None
    db.commit()
    db.refresh(session)
    return session

@router.post("/{session_id}/notes", response_model=schemas.NoteOut)
def add_note(session_id: str, payload: schemas.NoteCreate, db: DBSession = Depends(get_db)):
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    note = models.Note(session_id=session_id, **payload.model_dump())
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.get("/{session_id}/notes", response_model=list[schemas.NoteOut])
def list_notes(session_id: str, db: DBSession = Depends(get_db)):
    return (
        db.query(models.Note)
        .filter(models.Note.session_id == session_id)
        .order_by(models.Note.timestamp)
        .all()
    )


@router.post("/{session_id}/bugs", response_model=schemas.BugOut)
def log_bug(session_id: str, payload: schemas.BugCreate, db: DBSession = Depends(get_db)):
    """The evidence trail: the last N notes in this session are snapshotted
    onto the bug automatically, so reproduction context is captured live
    rather than reconstructed from memory after the fact."""
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    preceding_notes = (
        db.query(models.Note)
        .filter(models.Note.session_id == session_id)
        .order_by(models.Note.timestamp.desc())
        .limit(payload.trail_window)
        .all()
    )
    trail = [
        {"tag": n.tag, "content": n.content, "timestamp": n.timestamp.isoformat()}
        for n in reversed(preceding_notes)
    ]

    bug = models.Bug(
        session_id=session_id,
        charter_id=session.charter_id,
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
        environment=payload.environment,
        attachments=payload.attachments,
        reproduction_trail=trail,
    )
    db.add(bug)
    db.commit()
    db.refresh(bug)
    return bug


@router.get("/{session_id}/bugs", response_model=list[schemas.BugOut])
def list_bugs(session_id: str, db: DBSession = Depends(get_db)):
    return db.query(models.Bug).filter(models.Bug.session_id == session_id).all()


@bugs_list_router.get("/products/{product_id}/bugs", response_model=list[schemas.BugListItem])
def list_all_bugs(product_id: str, db: DBSession = Depends(get_db)):
    rows = (
        db.query(models.Bug, models.Charter)
        .join(models.Charter, models.Bug.charter_id == models.Charter.id)
        .filter(models.Charter.product_id == product_id)
        .order_by(models.Bug.created_at.desc())
        .all()
    )
    return [
        schemas.BugListItem(**schemas.BugOut.model_validate(b).model_dump(), charter_mission=c.mission_statement)
        for b, c in rows
    ]

@router.post("/{session_id}/end", response_model=schemas.DebriefOut)
async def end_session(session_id: str, db: DBSession = Depends(get_db)):
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.ended_at:
        raise HTTPException(400, "Session already ended")

    session.ended_at = utcnow()
    charter = db.get(models.Charter, session.charter_id)
    charter.status = "done"
    product = db.get(models.Product, charter.product_id)

    notes = db.query(models.Note).filter(models.Note.session_id == session_id).order_by(models.Note.timestamp).all()
    bugs = db.query(models.Bug).filter(models.Bug.session_id == session_id).all()

    def minute(ts) -> int:
        return max(0, int((ts - session.started_at).total_seconds() // 60))

    def bug_line(b: models.Bug) -> str:
        kind = b.kind or "unreviewed"
        oracle = f", oracle: {b.oracle_heuristic}" if b.oracle_heuristic else ""
        return f"- [{kind}, {b.severity}{oracle}] {b.title}"

    time_breakdown = _compute_time_breakdown(notes, session.ended_at)
    duration_minutes = round(
        ((session.ended_at - session.started_at).total_seconds() - (session.total_paused_seconds or 0)) / 60
    )
    debrief = models.Debrief(session_id=session_id, time_breakdown=time_breakdown)

    try:
        provider = get_provider(product.default_ai_provider)
        draft = await tasks.draft_debrief(
            provider,
            charter_mission=charter.mission_statement,
            risks=[{"ref": r.ref, "title": r.title, "failure_mode": r.failure_mode} for r in charter.risks],
            duration_minutes=duration_minutes,
            notes=[f"+{minute(n.timestamp)} min [{n.tag}] {n.content}" for n in notes],
            bugs=[bug_line(b) for b in bugs],
            time_breakdown=time_breakdown,
        )
        debrief.ai_draft = draft.past
        debrief.coverage_summary = draft.past
        debrief.open_questions = draft.open_questions
        debrief.charter_status = draft.charter_status
        debrief.charter_status_rationale = draft.charter_status_rationale
        debrief.results = draft.results
        debrief.obstacles = draft.obstacles
        debrief.outlook = [{**o.model_dump(), "charter_id": None} for o in draft.outlook]
        debrief.risks_observed = [{"text": r, "risk_id": None} for r in draft.risks_observed]
        debrief.ai_provider = provider.name
        debrief.ai_model = provider.model
        debrief.prompt_version = tasks.PROMPT_VERSIONS["debrief"]
    except ProviderError as err:
        # No API key, model down, or an invalid reply: the session still ends
        # and the debrief still saves; the tester writes it manually instead of
        # editing an AI draft. The reason is logged.
        logger.warning("Session %s ended without an AI debrief: %s", session_id, err)
        debrief.coverage_summary = None

    db.add(debrief)
    db.commit()
    db.refresh(debrief)
    return debrief
