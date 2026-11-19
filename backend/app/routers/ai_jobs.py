"""Start an AI step as a job, and look at it later (after a refresh, from another tab)."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session as DBSession

from app import ai_jobs, auth, models, schemas
from app.database import get_db

router = APIRouter(tags=["ai jobs"])


@router.post("/projects/{project_id}/ai-jobs", response_model=schemas.AiJobOut)
def start_job(project_id: str, payload: schemas.AiJobCreate, request: Request, background: BackgroundTasks,
              db: DBSession = Depends(get_db)):
    if not db.get(models.Project, project_id):
        raise HTTPException(404, "Project not found")
    try:
        ai_jobs.validate(payload.kind, payload.request)
    except ValidationError as err:
        raise HTTPException(422, err.errors(include_url=False, include_context=False)) from err
    user = auth.current_user(request, db)
    job, new = ai_jobs.start(db, project_id, payload.kind, payload.request, user.name)
    if new:
        background.add_task(ai_jobs.run, job.id)
    return ai_jobs.out(job)


@router.get("/projects/{project_id}/ai-jobs/latest", response_model=schemas.AiJobOut | None)
def latest_job(project_id: str, kind: str, story_id: str | None = None, db: DBSession = Depends(get_db)):
    job = ai_jobs.latest(db, project_id, kind, story_id=story_id)
    return ai_jobs.out(job) if job else None


@router.post("/ai-jobs/{job_id}/items", response_model=schemas.AiJobOut)
def mark_item(job_id: str, payload: schemas.AiJobItemUpdate, db: DBSession = Depends(get_db)):
    """A suggestion accepted (with the charter made from it) or dropped: it isn't offered again."""
    job = ai_jobs.settle(db, db.get(models.AiJob, job_id))
    if not job:
        raise HTTPException(404, "Job not found")
    if payload.charter_id:
        charter = db.get(models.Charter, payload.charter_id)
        if not charter or charter.project_id != job.project_id:
            raise HTTPException(404, "Charter not found in this project")
    try:
        return ai_jobs.out(ai_jobs.mark_item(db, job, payload.index, payload.status, payload.charter_id))
    except LookupError as err:
        raise HTTPException(404, str(err))


@router.get("/ai-jobs/{job_id}", response_model=schemas.AiJobOut)
def get_job(job_id: str, db: DBSession = Depends(get_db)):
    job = ai_jobs.settle(db, db.get(models.AiJob, job_id))
    if not job:
        raise HTTPException(404, "Job not found")
    return ai_jobs.out(job)
