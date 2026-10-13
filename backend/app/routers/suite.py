"""A project's regression tests together: list, run all, history, export."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session as DBSession

from app import test_data
from app import models, schemas
from app.config import settings
from app.database import get_db
from app.regression import suite as engine
from app.regression.plan import slug
from app.runner import manager
from app.timeutil import utcnow

router = APIRouter(tags=["regression suite"])


def _project_or_404(db: DBSession, project_id: str) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _run_or_404(db: DBSession, run_id: str) -> models.RegressionSuiteRun:
    run = db.get(models.RegressionSuiteRun, run_id)
    if not run:
        raise HTTPException(404, "Suite run not found")
    if run.status == "running" and run.id not in engine.ACTIVE:
        run.status, run.detail, run.finished_at = "error", "Interrupted: the backend restarted during the run.", utcnow()
        db.commit()
    return run


@router.get("/projects/{project_id}/regression-tests", response_model=list[schemas.SuiteTest])
def list_tests(project_id: str, db: DBSession = Depends(get_db)):
    """Every bug's latest regression test with its last replay."""
    from app.regression.triage import flakiness
    _project_or_404(db, project_id)
    history = engine.histories(db, project_id)
    out = []
    for bug, test in engine.latest_tests(db, project_id):
        outcome = (test.replay or {}).get("outcome")
        out.append(schemas.SuiteTest(
            bug_id=bug.id, bug_title=bug.title, test_id=test.id, filename=test.filename, known_failing=test.known_failing,
            base_url=test.base_url, outcome=outcome, meaning=engine.meaning(outcome, test.known_failing) if outcome else None,
            replayed_at=test.replayed_at, created_at=test.created_at,
            flaky=flakiness(history.get(test.id, []), test.known_failing)))
    return out


@router.post("/projects/{project_id}/regression-suite", response_model=schemas.SuiteRunOut)
def run_all(project_id: str, payload: schemas.SuiteRequest, db: DBSession = Depends(get_db)):
    """Replay every bug's latest regression test, one after another."""
    _project_or_404(db, project_id)
    running = db.query(models.RegressionSuiteRun).filter(models.RegressionSuiteRun.project_id == project_id,
                                                         models.RegressionSuiteRun.status == "running").all()
    if any(r.id in engine.ACTIVE for r in running):
        raise HTTPException(409, "The regression tests are already running for this project.")
    if payload.browser not in (None, "chromium"):
        from app import browsers
        if not browsers.installed(payload.browser):
            raise HTTPException(409, browsers.install_hint(payload.browser))
    pairs = engine.latest_tests(db, project_id)
    if not pairs:
        raise HTTPException(409, "No regression tests yet. Generate one from a reviewed bug on the Bugs page.")
    history = engine.histories(db, project_id)
    items = [{"bug_id": b.id, "bug_title": b.title, "test_id": t.id, "known_failing": t.known_failing,
              "plan": t.plan, "base_url": t.base_url, "history": history.get(t.id, [])} for b, t in pairs]
    run = models.RegressionSuiteRun(project_id=project_id, status="running",
                                    base_url=(payload.base_url or "").rstrip("/") or None, browser=payload.browser,
                                    total=len(items), results=[])
    db.add(run)
    db.commit()
    db.refresh(run)
    engine.ACTIVE.add(run.id)
    asyncio.run_coroutine_threadsafe(
        engine.run_suite(run.id, items, base_url=run.base_url, headless=True, executable_path=settings.runner_chromium_path,
                         browser=payload.browser, data=test_data.for_run(db, project_id)),
        manager.loop._get(),
    )
    return run


@router.get("/projects/{project_id}/suite-runs", response_model=list[schemas.SuiteRunOut])
def history(project_id: str, limit: int = 20, db: DBSession = Depends(get_db)):
    """Past runs, newest first."""
    _project_or_404(db, project_id)
    runs = (db.query(models.RegressionSuiteRun).filter(models.RegressionSuiteRun.project_id == project_id)
            .order_by(models.RegressionSuiteRun.created_at.desc()).limit(max(1, min(limit, 100))).all())
    return [_run_or_404(db, r.id) for r in runs]


@router.get("/suite-runs/{run_id}", response_model=schemas.SuiteRunOut)
def get_run(run_id: str, db: DBSession = Depends(get_db)):
    return _run_or_404(db, run_id)


@router.post("/suite-runs/{run_id}/cancel", response_model=schemas.SuiteRunOut)
def cancel(run_id: str, db: DBSession = Depends(get_db)):
    run = _run_or_404(db, run_id)
    if run.status != "running":
        raise HTTPException(409, f"The run has already finished ({run.status}).")
    run.cancel_requested = True  # stops before the next test
    db.commit()
    return run


@router.get("/projects/{project_id}/regression-tests/export")
def export(project_id: str, db: DBSession = Depends(get_db)):
    """Every test as one Playwright project (zip): tests, config, package.json, README."""
    project = _project_or_404(db, project_id)
    pairs = engine.latest_tests(db, project_id)
    if not pairs:
        raise HTTPException(409, "No regression tests to export yet.")
    return Response(engine.export_zip(project, pairs), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{slug(project.name)}-regression-tests.zip"'})
