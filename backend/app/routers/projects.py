"""Projects: what a tester works on. Everything else hangs off a project."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai.base import PROVIDER_NAMES
from app.database import get_db
from app.housekeeping import BusyError, delete_project
from app.timeutil import utcnow

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_or_404(db: DBSession, project_id: str) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.post("", response_model=schemas.ProjectOut)
def create_project(payload: schemas.ProjectCreate, db: DBSession = Depends(get_db)):
    project = models.Project(**payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[schemas.ProjectSummary])
def list_projects(db: DBSession = Depends(get_db)):
    """Projects for the Projects page, most recently worked on first."""
    projects = db.query(models.Project).all()
    risks = dict(db.query(models.Risk.project_id, func.count(models.Risk.id))
                 .filter(models.Risk.status != "retired").group_by(models.Risk.project_id).all())
    charters = dict(db.query(models.Charter.project_id, func.count(models.Charter.id))
                    .group_by(models.Charter.project_id).all())
    sessions = {pid: (n, last) for pid, n, last in
                db.query(models.Charter.project_id, func.count(models.Session.id), func.max(models.Session.started_at))
                .join(models.Session, models.Session.charter_id == models.Charter.id)
                .group_by(models.Charter.project_id).all()}
    bugs = dict(db.query(models.Charter.project_id, func.count(models.Bug.id))
                .join(models.Bug, models.Bug.charter_id == models.Charter.id)
                .group_by(models.Charter.project_id).all())
    out = []
    for p in projects:
        n_sessions, last = sessions.get(p.id, (0, None))
        summary = schemas.ProjectSummary.model_validate(p)
        summary.risk_count = risks.get(p.id, 0)
        summary.charter_count = charters.get(p.id, 0)
        summary.session_count = n_sessions
        summary.bug_count = bugs.get(p.id, 0)
        summary.last_activity = last
        out.append(summary)
    out.sort(key=lambda s: s.last_activity or s.created_at, reverse=True)
    return out


@router.get("/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: str, db: DBSession = Depends(get_db)):
    return _project_or_404(db, project_id)


@router.patch("/{project_id}", response_model=schemas.ProjectOut)
def update_project(project_id: str, payload: schemas.ProjectUpdate, db: DBSession = Depends(get_db)):
    """Change a project's settings: name, description, testing mission, AI provider,
    where its bugs are filed (GitHub repo, Jira project), the app's address and
    environments, and the default tester. Fields left out are unchanged; "" clears.
    (A project keeps the AI provider it was created with until changed here.)"""
    project = _project_or_404(db, project_id)
    changes = payload.model_dump(exclude_unset=True)
    provider = changes.get("default_ai_provider")
    if provider is not None and provider not in PROVIDER_NAMES:
        raise HTTPException(400, f"Unknown AI provider '{provider}'. Use one of: {', '.join(PROVIDER_NAMES)}")
    if changes.get("name") is not None:
        changes["name"] = changes["name"].strip()
        if not changes["name"]:
            raise HTTPException(422, "A project needs a name")
    for field in ("github_repo", "jira_project_key", "app_url", "description", "default_tester"):
        if field in changes:
            changes[field] = (changes[field] or "").strip() or None  # "" clears
    for field, value in changes.items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/archive", response_model=schemas.ProjectOut)
def archive_project(project_id: str, db: DBSession = Depends(get_db)):
    """Hide the project from the Projects page; nothing in it changes."""
    project = _project_or_404(db, project_id)
    project.archived_at = project.archived_at or utcnow()
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/unarchive", response_model=schemas.ProjectOut)
def unarchive_project(project_id: str, db: DBSession = Depends(get_db)):
    project = _project_or_404(db, project_id)
    project.archived_at = None
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", response_model=schemas.Deleted)
def remove_project(project_id: str, confirm: str = "", db: DBSession = Depends(get_db)):
    """Delete the project and everything in it, for good. `confirm` must be the
    project's name, so a stray request can't wipe a project."""
    project = _project_or_404(db, project_id)
    if confirm.strip() != project.name:
        raise HTTPException(422, "Type the project's name exactly to confirm.")
    try:
        return {"deleted": delete_project(db, project)}
    except BusyError as err:
        raise HTTPException(409, str(err)) from err
