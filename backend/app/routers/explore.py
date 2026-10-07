"""Exploration agent endpoints: let Mirmera explore a charter, follow it, stop it."""
import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
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


def app_map_text(db: DBSession, project_id: str) -> str | None:
    """The latest survey of the app, in a few lines for the agent."""
    from app import ai_jobs
    job = ai_jobs.latest(db, project_id, "app_map")
    if not job or job.status != "done" or not job.result:
        return None
    return "; ".join(p["summary"] for p in job.result.get("pages", [])[:20])[:1500] or None


def start_exploration(db: DBSession, charter: models.Charter, *, started_by: str, base_url: str | None = None,
                      provider_name: str | None = None, max_steps: int = 25,
                      conditions: dict | None = None) -> models.ExplorationRun:
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
    checker = provider_for(project, "explore_check", provider_name)  # may be a stronger model than the one that browses
    story = charter.stories[0] if charter.stories else None
    ctx = agent.CharterContext(
        mission=charter.mission_statement, test_ideas=list(charter.test_ideas or []), oracles=list(charter.oracles or []),
        risks=[{"ref": r.ref, "title": r.title, "failure_mode": r.failure_mode} for r in charter.risks],
        story=artifact_text(story) if story else None,
        criteria=agent.criteria_from(story.description) if story else [],
        earlier=agent.earlier_runs(db, charter),
        app_map=app_map_text(db, project.id),
    )
    session = models.Session(charter_id=charter.id, tester_id=agent.TESTER)
    db.add(session)
    db.flush()
    run = models.ExplorationRun(project_id=project.id, charter_id=charter.id, session_id=session.id, status="running",
                                start_url=base + "/", max_steps=max_steps, steps=[], bug_ids=[], started_by=started_by,
                                conditions=conditions or None,
                                ai_provider=provider.name, ai_model=provider.model, prompt_version=agent.PROMPT_VERSION)
    db.add(run)
    db.commit()
    db.refresh(run)

    agent.ACTIVE.add(run.id)  # before scheduling, so a status read right away doesn't see an orphan
    asyncio.run_coroutine_threadsafe(
        agent.run_agent(run.id, ctx, provider, session_id=session.id, charter_id=charter.id, start_url=run.start_url,
                        origins={origin(base)}, max_steps=max_steps, headless=True,
                        executable_path=settings.runner_chromium_path, folder=runner_api.session_folder(session.id),
                        checker=checker, conditions=conditions),
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
                                 provider_name=payload.provider, max_steps=payload.max_steps,
                                 conditions=payload.conditions.model_dump(exclude_none=True) if payload.conditions else None)
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


# ---------- watching the agent's browser ----------

@router.get("/exploration-runs/{run_id}/frame")
def live_frame(run_id: str, after: int = -1, db: DBSession = Depends(get_db)):
    """The agent's browser right now, as a JPEG. 204 when there is nothing newer than ?after=<seq>
    (so a page can check often for little), or nothing to show: not started yet, or stopped
    (X-Frame-Live: 0; its steps' pictures show what it did)."""
    _run_or_404(db, run_id)
    live = agent.LIVE.get(run_id)
    if not live or live.frame is None:
        return Response(status_code=204, headers={"X-Frame-Seq": "-1", "X-Frame-Live": "1" if live else "0"})
    if live.seq <= after:
        return Response(status_code=204, headers={"X-Frame-Seq": str(live.seq)})
    return Response(live.frame, media_type="image/jpeg",
                    headers={"X-Frame-Seq": str(live.seq), "X-Frame-Url": live.url, "Cache-Control": "no-store"})


@router.get("/exploration-runs/{run_id}/steps/{n}/frame")
def step_frame(run_id: str, n: int, db: DBSession = Depends(get_db)):
    """The page as the agent saw it when it chose step n."""
    run = _run_or_404(db, run_id)
    step = next((s for s in run.steps or [] if s.get("n") == n), None)
    name = (step or {}).get("frame")
    folder = runner_api.session_folder(run.session_id) if run.session_id else None
    path = (folder / name).resolve() if folder and name else None
    if not path or not path.is_relative_to(runner_api.evidence_root()) or not path.is_file():
        raise HTTPException(404, "No picture for this step")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/exploration-runs/{run_id}/video")
def video(run_id: str, request: Request, db: DBSession = Depends(get_db)):
    """The run's recording (WebM). Answers byte ranges, so the player can jump to any step."""
    run = _run_or_404(db, run_id)
    folder = runner_api.session_folder(run.session_id) if run.session_id else None
    path = (folder / run.video).resolve() if folder and run.video else None
    if not path or not path.is_relative_to(runner_api.evidence_root()) or not path.is_file():
        raise HTTPException(404, "No recording for this run")
    size = path.stat().st_size
    wanted = re.match(r"bytes=(\d*)-(\d*)$", request.headers.get("range", "").strip())
    if not wanted or wanted.groups() == ("", ""):
        return FileResponse(path, media_type="video/webm", headers={"Accept-Ranges": "bytes"})
    first, last = wanted.groups()
    start = int(first) if first else max(0, size - int(last))
    end = min(int(last), size - 1) if first and last else size - 1
    if start >= size or start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    with path.open("rb") as f:
        f.seek(start)
        chunk = f.read(end - start + 1)
    return Response(chunk, status_code=206, media_type="video/webm", headers={
        "Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes", "Content-Length": str(len(chunk))})


@router.post("/exploration-runs/{run_id}/hints", response_model=schemas.ExplorationRunOut)
def hint(run_id: str, payload: schemas.ExploreHint, user: models.User = Depends(current_user), db: DBSession = Depends(get_db)):
    """Pair with the agent: it reads the hint before its next step and tries it."""
    run = _run_or_404(db, run_id)
    if run.status != "running":
        raise HTTPException(409, f"Mirmera has stopped exploring ({run.status}); start it again to give it hints.")
    agent.add_hint(db, run, payload.text.strip(), by=user.name)
    return run
