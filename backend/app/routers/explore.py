"""Exploration agent endpoints: let Mirmera explore a charter, follow it, stop it."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.agent import explore as agent
from app.ai.routing import provider_for
from app.auth import current_user
from app.config import settings
from app.database import get_db
from app.regression.plan import origin
from app.routers import runner as runner_api
from app.runner import manager
from app.stories import artifact_text
from app.timeutil import utcnow

router = APIRouter(tags=["exploration agent"])


class ExploreProblem(Exception):
    """Exploring can't start; the message says why."""


def _run_or_404(db: DBSession, run_id: str) -> models.ExplorationRun:
    run = db.get(models.ExplorationRun, run_id)
    if not run:
        raise HTTPException(404, "Exploration run not found")
    if run.status == "running" and run.id not in agent.ACTIVE:
        run.status, run.detail, run.finished_at = "error", "Interrupted: the backend restarted during the run.", utcnow()
        db.commit()
    return run


def start_exploration(db: DBSession, charter: models.Charter, *, started_by: str, base_url: str | None = None,
                      provider_name: str | None = None, max_steps: int = 25) -> models.ExplorationRun:
    """Opens a session for the agent on the charter and starts it on the runner's loop."""
    project = charter.project
    base = (base_url or project.app_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ExploreProblem(f"{project.name} has no app URL, so Mirmera doesn't know where to explore. "
                             "Set it in the project's Settings.")
    running = (db.query(models.ExplorationRun)
               .filter(models.ExplorationRun.charter_id == charter.id, models.ExplorationRun.status == "running").all())
    if any(r.id in agent.ACTIVE for r in running):
        raise ExploreProblem("Mirmera is already exploring this charter.")

    provider = provider_for(project, "agent", provider_name)
    story = charter.stories[0] if charter.stories else None
    ctx = agent.CharterContext(
        mission=charter.mission_statement, test_ideas=list(charter.test_ideas or []), oracles=list(charter.oracles or []),
        risks=[{"ref": r.ref, "title": r.title, "failure_mode": r.failure_mode} for r in charter.risks],
        story=artifact_text(story) if story else None,
    )
    session = models.Session(charter_id=charter.id, tester_id=agent.TESTER)
    db.add(session)
    db.flush()
    run = models.ExplorationRun(project_id=project.id, charter_id=charter.id, session_id=session.id, status="running",
                                start_url=base + "/", max_steps=max_steps, steps=[], bug_ids=[], started_by=started_by,
                                ai_provider=provider.name, ai_model=provider.model, prompt_version=agent.PROMPT_VERSION)
    db.add(run)
    db.commit()
    db.refresh(run)

    agent.ACTIVE.add(run.id)  # before scheduling, so a status read right away doesn't see an orphan
    asyncio.run_coroutine_threadsafe(
        agent.run_agent(run.id, ctx, provider, session_id=session.id, charter_id=charter.id, start_url=run.start_url,
                        origins={origin(base)}, max_steps=max_steps, headless=True,
                        executable_path=settings.runner_chromium_path, folder=runner_api.session_folder(session.id)),
        manager.loop._get(),
    )
    return run


@router.post("/charters/{charter_id}/explore", response_model=schemas.ExplorationRunOut)
def explore(charter_id: str, payload: schemas.ExploreRequest, user: models.User = Depends(current_user),
            db: DBSession = Depends(get_db)):
    charter = db.get(models.Charter, charter_id)
    if not charter:
        raise HTTPException(404, "Charter not found")
    try:
        return start_exploration(db, charter, started_by=user.name, base_url=payload.base_url,
                                 provider_name=payload.provider, max_steps=payload.max_steps)
    except ExploreProblem as err:
        raise HTTPException(409, str(err))


@router.get("/charters/{charter_id}/exploration", response_model=schemas.ExplorationRunOut | None)
def latest(charter_id: str, db: DBSession = Depends(get_db)):
    run = (db.query(models.ExplorationRun).filter(models.ExplorationRun.charter_id == charter_id)
           .order_by(models.ExplorationRun.created_at.desc()).first())
    return _run_or_404(db, run.id) if run else None


@router.get("/exploration-runs/{run_id}", response_model=schemas.ExplorationRunOut)
def get_run(run_id: str, db: DBSession = Depends(get_db)):
    return _run_or_404(db, run_id)


@router.post("/exploration-runs/{run_id}/cancel", response_model=schemas.ExplorationRunOut)
def cancel(run_id: str, db: DBSession = Depends(get_db)):
    run = _run_or_404(db, run_id)
    if run.status != "running":
        raise HTTPException(409, f"The run has already finished ({run.status}).")
    run.cancel_requested = True  # the agent stops before its next step
    db.commit()
    return run
