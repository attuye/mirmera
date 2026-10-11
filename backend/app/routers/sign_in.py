"""Signing in to the app under test: how, and a try in a headless browser."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas, sign_in
from app.config import settings
from app.database import get_db

router = APIRouter(tags=["sign-in"])


def _project(db: DBSession, project_id: str) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.get("/projects/{project_id}/sign-in", response_model=schemas.AppSignInOut | None)
def get_sign_in(project_id: str, db: DBSession = Depends(get_db)):
    return sign_in.settings_of(_project(db, project_id))


@router.put("/projects/{project_id}/sign-in", response_model=schemas.AppSignInOut)
def set_sign_in(project_id: str, payload: schemas.AppSignIn, db: DBSession = Depends(get_db)):
    project = _project(db, project_id)
    sign_in.save(project, payload.model_dump())
    db.commit()
    return sign_in.settings_of(project)


@router.delete("/projects/{project_id}/sign-in", status_code=204)
def delete_sign_in(project_id: str, db: DBSession = Depends(get_db)):
    project = _project(db, project_id)
    project.sign_in = None
    db.commit()


@router.post("/projects/{project_id}/sign-in/try", response_model=schemas.AppSignInTry)
async def try_sign_in(project_id: str, db: DBSession = Depends(get_db)):
    project = _project(db, project_id)
    if not project.app_url and not (project.sign_in or {}).get("url", "").startswith("http"):
        raise HTTPException(400, "Set the app URL first: the sign-in page is opened on it")
    from app.runner import manager  # browsers run on the runner's loop (on Windows only it can start them)
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
        sign_in.try_it(project.id, settings.runner_chromium_path), manager.loop._get()))
