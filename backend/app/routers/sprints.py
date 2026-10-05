"""Sprints: a name, dates, a goal and the stories planned for them."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas, sprints
from app.database import get_db

router = APIRouter(tags=["sprints"])


def _sprint_or_404(db: DBSession, sprint_id: str) -> models.Sprint:
    sprint = db.get(models.Sprint, sprint_id)
    if not sprint:
        raise HTTPException(404, "Sprint not found")
    return sprint


def _stories(db: DBSession, project_id: str, ids: list[str]) -> list[models.Story]:
    ids = list(dict.fromkeys(ids))
    rows = db.query(models.Story).filter(models.Story.id.in_(ids), models.Story.project_id == project_id).all() if ids else []
    if len(rows) != len(ids):
        raise HTTPException(400, f"Unknown stories for this project: {sorted(set(ids) - {r.id for r in rows})}")
    return rows


@router.get("/projects/{project_id}/sprints", response_model=list[schemas.SprintSummary])
def list_sprints(project_id: str, db: DBSession = Depends(get_db)):
    """Newest first."""
    if not db.get(models.Project, project_id):
        raise HTTPException(404, "Project not found")
    rows = db.query(models.Sprint).filter(models.Sprint.project_id == project_id).order_by(models.Sprint.starts_on.desc()).all()
    return [sprints.summary(db, s) for s in rows]


@router.post("/projects/{project_id}/sprints", response_model=schemas.SprintDetail)
def create_sprint(project_id: str, payload: schemas.SprintCreate, db: DBSession = Depends(get_db)):
    if not db.get(models.Project, project_id):
        raise HTTPException(404, "Project not found")
    sprint = models.Sprint(project_id=project_id, name=payload.name.strip(), goal=(payload.goal or "").strip() or None,
                           starts_on=payload.starts_on, ends_on=payload.ends_on)
    db.add(sprint)
    db.flush()
    for story in _stories(db, project_id, payload.story_ids):
        story.sprint_id = sprint.id
    db.commit()
    db.refresh(sprint)
    return sprints.detail(db, sprint)


@router.get("/sprints/{sprint_id}", response_model=schemas.SprintDetail)
def get_sprint(sprint_id: str, db: DBSession = Depends(get_db)):
    return sprints.detail(db, _sprint_or_404(db, sprint_id))


@router.patch("/sprints/{sprint_id}", response_model=schemas.SprintDetail)
def update_sprint(sprint_id: str, payload: schemas.SprintUpdate, db: DBSession = Depends(get_db)):
    sprint = _sprint_or_404(db, sprint_id)
    changes = payload.model_dump(exclude_unset=True)
    if "goal" in changes:
        changes["goal"] = (changes["goal"] or "").strip() or None
    if changes.get("name") is not None:
        changes["name"] = changes["name"].strip()
    for field, value in changes.items():
        if value is not None or field == "goal":
            setattr(sprint, field, value)
    if sprint.ends_on < sprint.starts_on:
        raise HTTPException(422, "The sprint must end on or after the day it starts.")
    db.commit()
    db.refresh(sprint)
    return sprints.detail(db, sprint)


@router.put("/sprints/{sprint_id}/stories", response_model=schemas.SprintDetail)
def set_sprint_stories(sprint_id: str, payload: schemas.StoryLinks, db: DBSession = Depends(get_db)):
    """The sprint's stories. A story already in another sprint moves here (carried over)."""
    sprint = _sprint_or_404(db, sprint_id)
    chosen = _stories(db, sprint.project_id, payload.ids)
    for story in sprint.stories:
        if story not in chosen:
            story.sprint_id = None
    for story in chosen:
        story.sprint_id = sprint.id
    db.commit()
    db.refresh(sprint)
    return sprints.detail(db, sprint)


@router.delete("/sprints/{sprint_id}", response_model=schemas.Deleted)
def delete_sprint(sprint_id: str, db: DBSession = Depends(get_db)):
    """The sprint goes, with its test strategy versions; its stories stay, outside any sprint."""
    sprint = _sprint_or_404(db, sprint_id)
    for story in sprint.stories:
        story.sprint_id = None
    strategies = db.query(models.TestStrategy).filter(models.TestStrategy.sprint_id == sprint.id).delete(synchronize_session=False)
    db.delete(sprint)
    db.commit()
    return {"deleted": {"sprints": 1, **({"test_strategies": strategies} if strategies else {})}}
