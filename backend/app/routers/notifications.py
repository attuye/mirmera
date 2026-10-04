"""A project's notifications: which channels and events, a test message, and the log."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, notify, schemas
from app.keys import keys_for
from app.database import get_db

router = APIRouter(tags=["notifications"])


def _project_or_404(db: DBSession, project_id: str) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _overview(db: DBSession, project: models.Project) -> dict:
    recent = (db.query(models.NotificationLog).filter_by(project_id=project.id)
              .order_by(models.NotificationLog.created_at.desc()).limit(20).all())
    return {"events": [{"key": k, "label": v} for k, v in notify.EVENTS.items()], "settings": notify.prefs(project),
            "channels": notify.channel_status(keys_for(project)), "recent": recent}


@router.get("/projects/{project_id}/notifications", response_model=schemas.NotificationsOut)
def get_notifications(project_id: str, db: DBSession = Depends(get_db)):
    return _overview(db, _project_or_404(db, project_id))


@router.put("/projects/{project_id}/notifications", response_model=schemas.NotificationsOut)
def set_notifications(project_id: str, payload: schemas.NotificationSettings, db: DBSession = Depends(get_db)):
    project = _project_or_404(db, project_id)
    unknown = [e for e in payload.events if e not in notify.EVENTS]
    if unknown:
        raise HTTPException(400, f"Unknown events: {', '.join(unknown)}")
    project.notify = {"telegram_chat_id": payload.telegram_chat_id.strip(), "slack": payload.slack, "events": payload.events}
    db.commit()
    return _overview(db, project)


@router.post("/projects/{project_id}/notifications/test", response_model=schemas.NotificationsOut)
def send_test(project_id: str, db: DBSession = Depends(get_db)):
    """A test message to every channel set up for the project, whatever the events."""
    project = _project_or_404(db, project_id)
    if not notify.send(project_id, "test", "Test message", "Notifications from this project will arrive here."):
        raise HTTPException(409, "No channel is set up yet: add a Telegram chat id or turn on Slack, save, then try again.")
    db.expire_all()
    return _overview(db, project)
