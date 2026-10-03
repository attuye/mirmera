"""Isolate a bug: start a run, follow it, cancel it. The work is in app/regression/isolate.py."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.regression import isolate as engine
from app.regression import steps as bug_steps
from app.regression.plan import origin
from app.routers import runner as runner_api
from app.routers.trackers import _bug_or_404
from app.runner import manager
from app.timeutil import utcnow

router = APIRouter(tags=["isolate the bug"])


def _run_or_404(db: DBSession, run_id: str) -> models.IsolationRun:
    run = db.get(models.IsolationRun, run_id)
    if not run:
        raise HTTPException(404, "Isolation run not found")
    if run.status == "running" and run.id not in engine.ACTIVE:
        run.status, run.detail, run.finished_at = "error", "Interrupted: the backend restarted during the run.", utcnow()
        db.commit()
    return run


def default_symptom(bug: models.Bug, st: bug_steps.BugSteps) -> dict | None:
    """The failed request the steps lead to, else the text the report says actually shows."""
    if st.reach:
        return {"kind": "failed_request", "value": st.reach}
    if (bug.actual or "").strip():
        return {"kind": "text", "value": bug.actual.strip()[:300]}
    return None


@router.post("/bugs/{bug_id}/isolation", response_model=schemas.IsolationRunOut)
def start(bug_id: str, payload: schemas.IsolateRequest, db: DBSession = Depends(get_db)):
    bug = _bug_or_404(db, bug_id)
    running = db.query(models.IsolationRun).filter(models.IsolationRun.bug_id == bug_id,
                                                   models.IsolationRun.status == "running").all()
    if any(r.id in engine.ACTIVE for r in running):
        raise HTTPException(409, "This bug is already being isolated.")
    runner_api.sync_signals(db, bug.session_id)
    st, _ = bug_steps.steps_for(db, bug)
    if not st.steps or st.steps[0]["type"] != "goto":
        raise HTTPException(409, "There are no steps to replay. Add them under Steps to the bug first.")
    symptom = payload.symptom.model_dump() if payload.symptom else default_symptom(bug, st)
    if not symptom:
        raise HTTPException(409, "Say what shows the bug: a text on the page (or review the bug and fill in "
                                 "what actually happens).")
    plan_base = origin(st.steps[0]["url"])
    base_url = (payload.base_url or plan_base).rstrip("/")
    run = models.IsolationRun(bug_id=bug.id, status="running", base_url=base_url, symptom=symptom, variants=[])
    db.add(run)
    db.commit()
    db.refresh(run)
    engine.ACTIVE.add(run.id)  # before scheduling, so a read right away does not see an orphan
    asyncio.run_coroutine_threadsafe(
        engine.run_isolation(run.id, st.steps, symptom, base_url=base_url, plan_base=plan_base, headless=True,
                             executable_path=settings.runner_chromium_path),
        manager.loop._get(),
    )
    return run


@router.get("/isolation-runs/{run_id}", response_model=schemas.IsolationRunOut)
def get_run(run_id: str, db: DBSession = Depends(get_db)):
    return _run_or_404(db, run_id)


@router.get("/bugs/{bug_id}/isolation", response_model=schemas.IsolationRunOut | None)
def latest(bug_id: str, db: DBSession = Depends(get_db)):
    _bug_or_404(db, bug_id)
    run = (db.query(models.IsolationRun).filter(models.IsolationRun.bug_id == bug_id)
           .order_by(models.IsolationRun.created_at.desc()).first())
    return _run_or_404(db, run.id) if run else None


@router.post("/isolation-runs/{run_id}/cancel", response_model=schemas.IsolationRunOut)
def cancel(run_id: str, db: DBSession = Depends(get_db)):
    run = _run_or_404(db, run_id)
    if run.status != "running":
        raise HTTPException(409, f"The run has already finished ({run.status.replace('_', ' ')}).")
    run.cancel_requested = True
    db.commit()
    return run
