"""Dismissing a draft with a reason, undoing it, and what the project has dismissed (app/dismissals.py)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from app import dismissals, models, schemas
from app.auth import current_user
from app.database import get_db
from app.timeutil import utcnow
from app.trackers.fingerprint import fingerprint

router = APIRouter(tags=["dismissals"])


def _bug(db: DBSession, bug_id: str) -> models.Bug:
    bug = db.get(models.Bug, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    return bug


@router.post("/bugs/{bug_id}/dismiss", response_model=schemas.BugOut)
def dismiss(bug_id: str, payload: schemas.DismissRequest, request: Request, db: DBSession = Depends(get_db)):
    """Not a bug, and why. The same problem isn't drafted again in this project (unless it 'can't be
    reproduced', then it is, marked as dismissed before)."""
    bug = _bug(db, bug_id)
    if bug.filings:
        raise HTTPException(409, "This bug is already filed in a tracker: close it there instead.")
    by = current_user(request, db).name
    project_id = bug.charter.project_id
    old = db.query(models.Dismissal).filter(models.Dismissal.bug_id == bug.id).all()
    for d in old:
        db.delete(d)
    db.add(models.Dismissal(project_id=project_id, bug_id=bug.id, fingerprint=fingerprint(bug), title=bug.title,
                            reason=payload.reason, note=(payload.note or "").strip() or None, by=by))
    bug.dismissal = {"reason": payload.reason, "note": (payload.note or "").strip() or None, "by": by,
                     "at": utcnow().isoformat()}
    bug.last_change = f"Dismissed by {by}: {dismissals.REASONS[payload.reason]}"
    db.commit()
    db.refresh(bug)
    return bug


@router.delete("/bugs/{bug_id}/dismiss", response_model=schemas.BugOut)
def undo(bug_id: str, request: Request, db: DBSession = Depends(get_db)):
    """A draft again; Mirmera may draft the problem again."""
    bug = _bug(db, bug_id)
    for d in db.query(models.Dismissal).filter(models.Dismissal.bug_id == bug.id).all():
        db.delete(d)
    bug.dismissal = None
    bug.last_change = f"Dismissal undone by {current_user(request, db).name}"
    db.commit()
    db.refresh(bug)
    return bug


@router.get("/projects/{project_id}/dismissals", response_model=list[schemas.DismissalOut])
def listed(project_id: str, db: DBSession = Depends(get_db)):
    """What Mirmera won't draft again in this project, newest first."""
    return (db.query(models.Dismissal).filter(models.Dismissal.project_id == project_id)
            .order_by(models.Dismissal.created_at.desc()).all())


@router.delete("/dismissals/{dismissal_id}", status_code=204)
def forget(dismissal_id: str, db: DBSession = Depends(get_db)):
    """Forget a dismissal (its bug may be gone): the problem can be drafted again."""
    d = db.get(models.Dismissal, dismissal_id)
    if not d:
        raise HTTPException(404, "Not found")
    bug = db.get(models.Bug, d.bug_id) if d.bug_id else None
    if bug:
        bug.dismissal = None
    db.delete(d)
    db.commit()
