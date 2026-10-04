"""Test-idea nudges for a running session: list, hide, and "tried it"."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, nudges, schemas
from app.database import get_db
from app.routers import runner as runner_api

router = APIRouter(tags=["nudges"])


def _session_or_404(db: DBSession, session_id: str) -> models.Session:
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session


def _hide(db: DBSession, session: models.Session, nudge_id: str) -> None:
    hidden = list(session.dismissed_nudges or [])
    if nudge_id not in hidden:
        session.dismissed_nudges = hidden + [nudge_id]  # a new list, so the JSON column saves


@router.get("/sessions/{session_id}/nudges", response_model=list[schemas.NudgeOut])
def list_nudges(session_id: str, db: DBSession = Depends(get_db)):
    """Ideas from what this session has done so far, most useful first."""
    session = _session_or_404(db, session_id)
    if session.ended_at:
        return []
    runner_api.sync_signals(db, session_id)  # what the recorded browser saw since the last look
    return nudges.build(db, session)


@router.post("/sessions/{session_id}/nudges/{nudge_id}/dismiss", status_code=204)
def dismiss(session_id: str, nudge_id: str, db: DBSession = Depends(get_db)):
    """Not now: hidden for the rest of this session."""
    _hide(db, _session_or_404(db, session_id), nudge_id)
    db.commit()


@router.post("/sessions/{session_id}/nudges/{nudge_id}/tried", response_model=schemas.NoteOut)
def tried(session_id: str, nudge_id: str, payload: schemas.NudgeTried, db: DBSession = Depends(get_db)):
    """Logs a test note saying what was tried, and hides the nudge."""
    session = _session_or_404(db, session_id)
    if session.ended_at:
        raise HTTPException(409, "The session has ended.")
    note = models.Note(session_id=session_id, tag="test", content=payload.note.strip())
    db.add(note)
    _hide(db, session, nudge_id)
    db.commit()
    db.refresh(note)
    return note
