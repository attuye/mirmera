import logging
from app.timeutil import utcnow

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from app import auth, models, schemas
from app.ai import tasks
from app.ai.base import ProviderError
from app.ai.routing import provider_for
from app.database import SessionLocal, get_db
from app.evidence_text import browser_context
from app.session_text import bug_line, note_line, riskiest_first
from app.routers import runner as runner_api

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
def start_session(payload: schemas.SessionCreate, request: Request, db: DBSession = Depends(get_db)):
    charter = db.get(models.Charter, payload.charter_id)
    if not charter:
        raise HTTPException(404, "Charter not found")
    auth.check_project(request, db, charter.project_id)

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
    db.flush()
    if payload.evidence_ids:
        rows = db.query(models.Evidence).filter(models.Evidence.id.in_(payload.evidence_ids),
                                                models.Evidence.session_id == session_id).all()
        if len(rows) != len(set(payload.evidence_ids)):
            raise HTTPException(400, "Evidence from another session can't be attached to this bug")
        for row in rows:
            row.bug_id = bug.id
    db.commit()

    # With the browser running, the bug gets a screenshot of what was on screen.
    # Best effort: the bug is already saved, and a failed screenshot must not lose it.
    browser = runner_api.sync_signals(db, session_id)
    if browser:
        try:
            runner_api.take_screenshot(db, session_id, browser, caption=f"When logged: {bug.title}", bug_id=bug.id)
        except Exception as err:
            logger.warning("Bug %s saved without a screenshot: %s", bug.id, err)
    db.refresh(bug)
    return bug


@router.get("/{session_id}/bugs", response_model=list[schemas.BugOut])
def list_bugs(session_id: str, db: DBSession = Depends(get_db)):
    return db.query(models.Bug).filter(models.Bug.session_id == session_id).all()


@bugs_list_router.get("/projects/{project_id}/bugs", response_model=list[schemas.BugListItem])
def list_all_bugs(project_id: str, db: DBSession = Depends(get_db)):
    rows = (
        db.query(models.Bug, models.Charter)
        .join(models.Charter, models.Bug.charter_id == models.Charter.id)
        .filter(models.Charter.project_id == project_id)
        .order_by(models.Bug.created_at.desc())
        .all()
    )
    return [
        schemas.BugListItem(**schemas.BugOut.model_validate(b).model_dump(), charter_mission=c.mission_statement)
        for b, c in rows
    ]

@bugs_list_router.get("/projects/{project_id}/agent-record")
def agent_record(project_id: str, db: DBSession = Depends(get_db)):
    """What testers made of the exploration agent's drafts: overall, by who judged them, and per run."""
    from app.agent.record import record
    return record(db, project_id)


@router.post("/{session_id}/end", response_model=schemas.DebriefOut)
async def end_session(session_id: str, background: BackgroundTasks, db: DBSession = Depends(get_db)):
    """Ends the session at once and answers with its debrief, still being written: the AI
    drafts it in the background (a local model takes minutes), and the debrief page shows
    that and fills in when it is done."""
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.ended_at:
        raise HTTPException(400, "Session already ended")

    # Close the browser first, so its trace, HAR and last signals are saved.
    await runner_api.stop_runner_async(db, session_id)

    session.ended_at = utcnow()
    if session.paused_at:  # ended while paused: the pause doesn't count as testing time
        session.total_paused_seconds = (session.total_paused_seconds or 0) + (session.ended_at - session.paused_at).total_seconds()
        session.paused_at = None
    charter = db.get(models.Charter, session.charter_id)
    still_open = (db.query(models.Session)
                  .filter(models.Session.charter_id == charter.id, models.Session.id != session_id, models.Session.ended_at.is_(None))
                  .count())
    charter.status = "active" if still_open else "done"  # done when its last open session ends

    notes = db.query(models.Note).filter(models.Note.session_id == session_id).order_by(models.Note.timestamp).all()
    bugs = db.query(models.Bug).filter(models.Bug.session_id == session_id).all()
    time_breakdown = _compute_time_breakdown(notes, session.ended_at)
    recorded = (
        db.query(models.Evidence).filter(models.Evidence.session_id == session_id)
        .order_by(models.Evidence.captured_at).all()
    )
    draft_input = dict(
        charter_mission=charter.mission_statement,
        risks=[{"ref": r.ref, "title": r.title, "failure_mode": r.failure_mode} for r in riskiest_first(charter.risks)],
        duration_minutes=round(
            ((session.ended_at - session.started_at).total_seconds() - (session.total_paused_seconds or 0)) / 60),
        notes=[note_line(n, session.started_at) for n in notes],
        bugs=[bug_line(b) for b in bugs],
        time_breakdown=time_breakdown,
        browser=browser_context(recorded, session.started_at, used=bool(recorded)),
    )
    debrief = models.Debrief(session_id=session_id, drafting=True, time_breakdown=time_breakdown)
    db.add(debrief)
    db.commit()
    db.refresh(debrief)
    background.add_task(write_debrief, debrief.id, charter.project_id, draft_input)
    return debrief


async def write_debrief(debrief_id: str, project_id: str, draft_input: dict) -> None:
    """The AI's draft of a debrief, after the session has ended. Uses its own database
    session and holds no write while the AI thinks."""
    with SessionLocal() as db:
        project = db.get(models.Project, project_id)
        fields: dict = {}
        try:
            provider = provider_for(project, "debrief")
            db.commit()  # only reads so far: end that transaction before the AI call
            draft = await tasks.draft_debrief(provider, **draft_input)
            fields = dict(
                ai_draft=draft.past, coverage_summary=draft.past, open_questions=draft.open_questions,
                charter_status=draft.charter_status, charter_status_rationale=draft.charter_status_rationale,
                results=draft.results, obstacles=draft.obstacles,
                outlook=[{**o.model_dump(), "charter_id": None} for o in draft.outlook],
                risks_observed=[{"text": r, "risk_id": None} for r in draft.risks_observed],
                ai_provider=provider.name, ai_model=provider.model, prompt_version=tasks.PROMPT_VERSIONS["debrief"],
            )
        except ProviderError as err:
            # No API key, model down, or an invalid reply: the debrief stays empty and
            # the tester writes it instead of editing an AI draft. The reason is logged.
            logger.warning("Debrief %s has no AI draft: %s", debrief_id, err)
        except Exception:
            logger.exception("Debrief %s: the AI draft failed", debrief_id)
        finally:
            debrief = db.get(models.Debrief, debrief_id)
            if debrief:  # the session may have been deleted meanwhile
                for k, v in fields.items():
                    setattr(debrief, k, v)
                debrief.drafting = False  # whatever happened, the tester can now write or edit it
                db.commit()


@router.delete("/{session_id}", response_model=schemas.Deleted)
def delete_session(session_id: str, db: DBSession = Depends(get_db)):
    """Delete a session with its notes, bugs, debrief, evidence and recorded files."""
    from app.housekeeping import BusyError, delete_session as remove
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    try:
        return {"deleted": remove(db, session)}
    except BusyError as err:
        raise HTTPException(409, str(err)) from err
