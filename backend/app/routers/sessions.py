from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai.base import get_provider
from app.database import get_db

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
    session.paused_at = datetime.utcnow()
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
    session.total_paused_seconds += (datetime.utcnow() - session.paused_at).total_seconds()
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
        schemas.BugListItem(
            id=b.id, session_id=b.session_id, charter_mission=c.mission_statement,
            title=b.title, severity=b.severity, status=b.status,
            reproduction_trail=b.reproduction_trail, created_at=b.created_at,
        )
        for b, c in rows
    ]

@router.post("/{session_id}/end", response_model=schemas.DebriefOut)
async def end_session(session_id: str, db: DBSession = Depends(get_db)):
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.ended_at:
        raise HTTPException(400, "Session already ended")

    session.ended_at = datetime.utcnow()
    charter = db.get(models.Charter, session.charter_id)
    charter.status = "done"
    product = db.get(models.Product, charter.product_id)

    notes = db.query(models.Note).filter(models.Note.session_id == session_id).order_by(models.Note.timestamp).all()
    bugs = db.query(models.Bug).filter(models.Bug.session_id == session_id).all()

    notes_text = "\n".join(f"[{n.tag}] {n.timestamp.isoformat()} — {n.content}" for n in notes)
    bugs_text = "\n".join(f"- ({b.severity}) {b.title}" for b in bugs) or "(none)"

    tag_counts: dict[str, int] = {}
    for n in notes:
        tag_counts[n.tag] = tag_counts.get(n.tag, 0) + 1

    debrief = models.Debrief(
        session_id=session_id,
        time_breakdown=_compute_time_breakdown(notes, session.ended_at),
    )

    provider = get_provider(product.default_ai_provider)
    try:
        draft = await provider.draft_debrief({
            "charter_mission": charter.mission_statement,
            "notes_text": notes_text or "(no notes logged)",
            "bugs_text": bugs_text,
        })
        debrief.ai_draft = draft.coverage_summary
        debrief.coverage_summary = draft.coverage_summary
        debrief.open_questions = draft.open_questions
    except Exception:
        # Provider stub not implemented, no API key, or a transient failure —
        # the session still ends and the debrief still saves; the tester
        # just writes the coverage summary manually instead of editing an AI draft.
        debrief.coverage_summary = None

    db.add(debrief)
    db.commit()
    db.refresh(debrief)
    return debrief
