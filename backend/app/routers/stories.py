"""User stories: import from Jira or GitHub, or write one here; link risks and charters;
see the testing under them and what is still left."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas, sprints, stories
from app.database import get_db
from app.routers.risks import create_risk

router = APIRouter(tags=["stories"])


def _project_or_404(db: DBSession, project_id: str) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _story_or_404(db: DBSession, story_id: str) -> models.Story:
    story = db.get(models.Story, story_id)
    if not story:
        raise HTTPException(404, "Story not found")
    return story


def _check_key_free(db: DBSession, project_id: str, key: str | None, story_id: str | None = None) -> None:
    if not key:
        return
    clash = db.query(models.Story).filter(models.Story.project_id == project_id, models.Story.key == key,
                                          models.Story.id != story_id).first()
    if clash:
        raise HTTPException(409, f"{key} is already a story in this project: \"{clash.title}\".")


@router.get("/projects/{project_id}/stories", response_model=list[schemas.StorySummary])
def list_stories(project_id: str, db: DBSession = Depends(get_db)):
    """Open stories first, newest first within each."""
    _project_or_404(db, project_id)
    rows = db.query(models.Story).filter(models.Story.project_id == project_id).all()
    rows.sort(key=lambda st: st.created_at, reverse=True)
    rows.sort(key=lambda st: bool(st.closed))
    return [stories.summary(st) for st in rows]


@router.post("/projects/{project_id}/stories", response_model=schemas.StoryDetail)
def create_story(project_id: str, payload: schemas.StoryCreate, db: DBSession = Depends(get_db)):
    _project_or_404(db, project_id)
    key = (payload.key or "").strip() or None
    _check_key_free(db, project_id, key)
    story = models.Story(project_id=project_id, key=key, title=payload.title.strip(),
                         description=(payload.description or "").strip() or None, url=(payload.url or "").strip() or None)
    db.add(story)
    db.commit()
    db.refresh(story)
    return stories.detail(db, story)


@router.post("/projects/{project_id}/stories/import", response_model=schemas.StoryDetail)
async def import_story(project_id: str, payload: schemas.StoryImport, db: DBSession = Depends(get_db)):
    """A story from Jira (KYC-12) or GitHub (#45): title, description, address and status, and
    from Jira its sprint (made here when it's new; see sprints.from_jira)."""
    project = _project_or_404(db, project_id)
    try:
        data = await stories.fetch(project, payload.ref)
    except stories.StoryProblem as err:
        raise HTTPException(400, str(err))
    _check_key_free(db, project_id, data["key"])
    sprint = data.pop("sprint")
    story = models.Story(project_id=project_id, **data)
    db.add(story)
    db.flush()
    note = sprints.from_jira(db, story, sprint) if data["tracker"] == "jira" else None
    db.commit()
    db.refresh(story)
    return {**stories.detail(db, story), "sprint_note": note}


@router.get("/stories/{story_id}", response_model=schemas.StoryDetail)
def get_story(story_id: str, db: DBSession = Depends(get_db)):
    return stories.detail(db, _story_or_404(db, story_id))


@router.patch("/stories/{story_id}", response_model=schemas.StoryDetail)
def update_story(story_id: str, payload: schemas.StoryUpdate, db: DBSession = Depends(get_db)):
    story = _story_or_404(db, story_id)
    changes = payload.model_dump(exclude_unset=True)
    if "key" in changes:
        changes["key"] = (changes["key"] or "").strip() or None
        _check_key_free(db, story.project_id, changes["key"], story.id)
    for field in ("description", "url"):
        if field in changes:
            changes[field] = (changes[field] or "").strip() or None
    if changes.get("sprint_id"):
        sprint = db.get(models.Sprint, changes["sprint_id"])
        if not sprint or sprint.project_id != story.project_id:
            raise HTTPException(400, "Unknown sprint for this project")
    for field, value in changes.items():
        setattr(story, field, value)
    db.commit()
    db.refresh(story)
    return stories.detail(db, story)


@router.post("/stories/{story_id}/refresh", response_model=schemas.StoryDetail)
async def refresh_story(story_id: str, db: DBSession = Depends(get_db)):
    """Read the story again from its tracker: title, description, status and (Jira) sprint may have changed."""
    story = _story_or_404(db, story_id)
    if not story.tracker or not story.key:
        raise HTTPException(400, "This story was written here, so there is nothing to refresh it from.")
    project = db.get(models.Project, story.project_id)
    try:
        data = await stories.fetch(project, story.key)
    except stories.StoryProblem as err:
        raise HTTPException(400, str(err))
    for field in ("title", "description", "url", "tracker_status", "closed"):
        setattr(story, field, data[field])
    note = sprints.from_jira(db, story, data["sprint"]) if story.tracker == "jira" else None
    db.commit()
    db.refresh(story)
    return {**stories.detail(db, story), "sprint_note": note}


@router.delete("/stories/{story_id}", response_model=schemas.Deleted)
def delete_story(story_id: str, db: DBSession = Depends(get_db)):
    """The story goes; its risks, charters and sessions stay in the project."""
    story = _story_or_404(db, story_id)
    stories.unlink_all(db, story_ids=[story.id])
    db.delete(story)
    db.commit()
    return {"deleted": {"stories": 1}}


def _same_project(db: DBSession, model, ids: list[str], project_id: str, what: str) -> list:
    rows = db.query(model).filter(model.id.in_(ids), model.project_id == project_id).all() if ids else []
    missing = set(ids) - {r.id for r in rows}
    if missing:
        raise HTTPException(400, f"Unknown {what} for this project: {sorted(missing)}")
    return rows


@router.put("/stories/{story_id}/risks", response_model=schemas.StoryDetail)
def set_story_risks(story_id: str, payload: schemas.StoryLinks, db: DBSession = Depends(get_db)):
    story = _story_or_404(db, story_id)
    story.risks = _same_project(db, models.Risk, list(dict.fromkeys(payload.ids)), story.project_id, "risks")
    db.commit()
    db.refresh(story)
    return stories.detail(db, story)


@router.put("/stories/{story_id}/charters", response_model=schemas.StoryDetail)
def set_story_charters(story_id: str, payload: schemas.StoryLinks, db: DBSession = Depends(get_db)):
    story = _story_or_404(db, story_id)
    story.charters = _same_project(db, models.Charter, list(dict.fromkeys(payload.ids)), story.project_id, "charters")
    db.commit()
    db.refresh(story)
    return stories.detail(db, story)


@router.post("/stories/{story_id}/risks/new", response_model=schemas.StoryDetail)
def new_story_risk(story_id: str, payload: schemas.RiskCreate, db: DBSession = Depends(get_db)):
    """A risk the tester adds while looking at the story: a project risk, linked to the story."""
    story = _story_or_404(db, story_id)
    risk = create_risk(story.project_id, payload, db)
    story.risks.append(risk)
    db.commit()
    db.refresh(story)
    return stories.detail(db, story)


@router.post("/stories/{story_id}/artifact", response_model=schemas.ArtifactOut)
def story_artifact(story_id: str, db: DBSession = Depends(get_db)):
    """The story as an artifact, so the AI can suggest charters for it. Kept up to date with the story."""
    story = _story_or_404(db, story_id)
    label = (f"{story.key}: {story.title}" if story.key else f"Story: {story.title}")[:200]
    artifact = db.get(models.Artifact, story.artifact_id) if story.artifact_id else None
    if artifact is None:
        artifact = models.Artifact(project_id=story.project_id, type="spec", label=label)
        db.add(artifact)
    text = stories.artifact_text(story)
    if artifact.raw_content != text:
        artifact.ai_summary = None  # the cached summary was of the old text
    artifact.label, artifact.raw_content, artifact.file_ref = label, text, story.url
    db.flush()
    story.artifact_id = artifact.id
    db.commit()
    db.refresh(artifact)
    return artifact
