"""Retest bugs whose issues were closed: check now, and the history."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.regression import retest as engine
from app.runner import manager

router = APIRouter(tags=["retests"])


def _project_or_404(db: DBSession, project_id: str) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _out(db: DBSession, retests: list[models.Retest]) -> list[schemas.RetestOut]:
    engine.settle_orphans(db, retests)
    titles = dict(db.query(models.Bug.id, models.Bug.title).filter(models.Bug.id.in_({r.bug_id for r in retests})).all()) if retests else {}
    return [schemas.RetestOut.model_validate(r).model_copy(update={"bug_title": titles.get(r.bug_id, "")}) for r in retests]


def start_replays(ids: list[str], base_url: str | None) -> None:
    if ids:
        asyncio.run_coroutine_threadsafe(
            engine.run_retests(ids, base_url=base_url, headless=True, executable_path=settings.runner_chromium_path),
            manager.loop._get(),
        )


@router.post("/projects/{project_id}/retests/check", response_model=schemas.RetestCheckOut)
async def check(project_id: str, payload: schemas.RetestRequest, db: DBSession = Depends(get_db)):
    """Look up every issue filed from the project; replay the test of each bug whose issue
    was closed since the last check, and comment on the issue with the result."""
    project = _project_or_404(db, project_id)
    filed = len(engine.filings_of(db, project_id))
    if not filed:
        raise HTTPException(409, "No bugs from this project have been filed to an issue tracker yet.")
    result = await engine.check_project(db, project)
    start_replays(result.to_replay, (payload.base_url or "").rstrip("/") or None)
    return schemas.RetestCheckOut(checked=result.checked, filed=filed, retests=_out(db, result.retests), problems=result.problems)


@router.get("/projects/{project_id}/retests", response_model=list[schemas.RetestOut])
def history(project_id: str, bug_id: str | None = None, limit: int = 50, db: DBSession = Depends(get_db)):
    """Retests, newest first; bug_id narrows them to one bug."""
    _project_or_404(db, project_id)
    q = db.query(models.Retest).filter(models.Retest.project_id == project_id)
    if bug_id:
        q = q.filter(models.Retest.bug_id == bug_id)
    return _out(db, q.order_by(models.Retest.created_at.desc()).limit(max(1, min(limit, 200))).all())


async def poll_forever(minutes: int) -> None:
    """RETEST_POLL_MINUTES: check every project that has filed issues, every N minutes."""
    from app.database import SessionLocal
    import logging
    log = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(minutes * 60)
        try:
            with SessionLocal() as db:
                projects = db.query(models.Project).filter(models.Project.archived_at.is_(None)).all()
                for project in projects:
                    if not engine.filings_of(db, project.id):
                        continue
                    result = await engine.check_project(db, project)
                    start_replays(result.to_replay, None)
                    for problem in result.problems:
                        log.warning("Retest check (%s): %s", project.name, problem)
        except Exception:
            log.exception("Retest check failed; trying again in %s minutes", minutes)


@router.post("/mock-trackers/issue-state")
def set_mock_issue_state(payload: schemas.MockIssueState):
    """With MOCK_TRACKERS=true only: close or reopen an issue in the in-memory tracker,
    to try "Check closed issues" without a real GitHub or Jira."""
    if not settings.mock_trackers:
        raise HTTPException(404, "Only available with MOCK_TRACKERS=true.")
    from app.trackers.mock import MockTracker
    from app.trackers import TrackerError
    try:
        MockTracker(payload.tracker, payload.target).set_closed(payload.issue_key, payload.closed)
    except TrackerError as err:
        raise HTTPException(404, str(err))
    return {"issue_key": payload.issue_key, "closed": payload.closed}
