"""Reproduction agent endpoints: start a run on a bug, follow it, cancel it,
turn what it found into a regression test."""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.agent import reproduce as agent
from app.ai import tasks
from app.ai.base import get_provider
from app.config import settings
from app.database import get_db
from app.evidence_text import browser_context
from app.regression.plan import is_same_origin, origin, slug
from app.regression.render import render
from app.routers import runner as runner_api
from app.regression import steps as bug_steps
from app.routers.trackers import _bug_or_404
from app.runner import manager
from app.timeutil import utcnow
from app.trackers.report import ORACLE_NAMES

router = APIRouter(tags=["reproduction agent"])

# The symptom the agent proved, turned around into what the test should check.
REGRESSION_ASSERTION = {
    "failed_request": lambda s: {"kind": "no_failed_requests", "url_contains": s["value"]},
    "console_error": lambda s: {"kind": "no_console_errors"},
    "page_error": lambda s: {"kind": "no_page_errors"},
    "text_visible": lambda s: {"kind": "hidden", "locator": {"method": "text", "value": s["value"]}},
    "text_absent": lambda s: {"kind": "visible", "locator": {"method": "text", "value": s["value"]}},
}


def _run_or_404(db: DBSession, run_id: str) -> models.ReproductionRun:
    run = db.get(models.ReproductionRun, run_id)
    if not run:
        raise HTTPException(404, "Reproduction run not found")
    if run.status == "running" and run.id not in agent.ACTIVE:
        # Nobody is working on it any more (the server restarted mid-run).
        run.status, run.detail, run.finished_at = "error", "Interrupted: the backend restarted during the run.", utcnow()
        db.commit()
    return run


def _out(run: models.ReproductionRun) -> schemas.ReproductionRunOut:
    return schemas.ReproductionRunOut.model_validate(run)


@router.post("/bugs/{bug_id}/reproduce", response_model=schemas.ReproductionRunOut)
def start(bug_id: str, payload: schemas.ReproduceRequest, db: DBSession = Depends(get_db)):
    bug = _bug_or_404(db, bug_id)
    running = (db.query(models.ReproductionRun)
               .filter(models.ReproductionRun.bug_id == bug_id, models.ReproductionRun.status == "running").all())
    if any(r.id in agent.ACTIVE for r in running):
        raise HTTPException(409, "The agent is already working on this bug.")

    runner_api.sync_signals(db, bug.session_id)
    # Only this bug's own recording (since the previous bug), and its steps as the hints:
    # edited by the tester, picked from the recording, or the recorded actions.
    st, rec = bug_steps.steps_for(db, bug)
    pages = list(dict.fromkeys([s["url"] for s in st.steps if s["type"] == "goto"] + rec.pages))
    base_url = payload.base_url or (origin(pages[0]) if pages else None)
    if not base_url:
        raise HTTPException(409, "Nothing was recorded for this bug, so give the app's address (base URL) to start from.")
    base_url = base_url.rstrip("/")
    # Start where the tester started, on the address given (e.g. staging instead of local).
    start_url = base_url + "/"
    if pages:
        first = pages[0]
        start_url = base_url + (first[len(origin(first)):] or "/") if not is_same_origin(first, base_url) else first
    origins = {origin(base_url)}

    project = db.get(models.Project, bug.charter.project_id)
    provider = get_provider(payload.provider or project.default_ai_provider)
    evidence = [e for e in runner_api.evidence_for_bug(db, bug) if e.kind != "action"]
    ctx = agent.BugContext(
        report={"title": bug.title, "kind": bug.kind or "unreviewed", "description": bug.description,
                "steps": bug.steps_to_reproduce or [], "expected": bug.expected, "actual": bug.actual,
                "oracle": bug.oracle_heuristic, "why_its_a_problem": bug.oracle_explanation,
                "notes_before": [n.get("content") for n in (bug.reproduction_trail or [])]},
        hints=[{k: v for k, v in s["action"].items() if k not in ("in_frame", "summary")}
               for s in st.steps if s["type"] == "action"][-40:],
        pages=pages,
        signals=browser_context(evidence, bug.session.started_at, used=bool(evidence)) if evidence else "",
    )
    # The known steps are replayed first (no AI); the bug is checked by what shows it.
    replay = list(st.steps) if payload.replay_known_steps and st.steps and st.steps[0]["type"] == "goto" else None
    found = bug_steps.bug_symptom(bug, st)
    symptom = ({"kind": "failed_request" if found["kind"] == "failed_request" else "text_visible", "value": found["value"]}
               if found else None)
    run = models.ReproductionRun(
        bug_id=bug.id, status="running", base_url=base_url, start_url=start_url,
        max_steps=payload.max_steps, runs=payload.runs, steps=[],
        ai_provider=provider.name, ai_model=provider.model, prompt_version=agent.PROMPT_VERSION,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    shot = runner_api.session_folder(bug.session_id) / f"reproduction-{run.id[:8]}.png"
    agent.ACTIVE.add(run.id)  # before scheduling, so a status read right away does not see an orphan
    asyncio.run_coroutine_threadsafe(
        agent.run_agent(run.id, ctx, provider, start_url=start_url, origins=origins, max_steps=payload.max_steps,
                        runs=payload.runs, headless=True, executable_path=settings.runner_chromium_path,
                        screenshot_path=shot, replay_steps=replay, replay_symptom=symptom),
        manager.loop._get(),
    )
    return _out(run)


@router.get("/reproduction-runs/{run_id}", response_model=schemas.ReproductionRunOut)
def get_run(run_id: str, db: DBSession = Depends(get_db)):
    return _out(_run_or_404(db, run_id))


@router.get("/bugs/{bug_id}/reproduction", response_model=schemas.ReproductionRunOut | None)
def latest(bug_id: str, db: DBSession = Depends(get_db)):
    _bug_or_404(db, bug_id)
    run = (db.query(models.ReproductionRun).filter(models.ReproductionRun.bug_id == bug_id)
           .order_by(models.ReproductionRun.created_at.desc()).first())
    return _out(_run_or_404(db, run.id)) if run else None


@router.post("/reproduction-runs/{run_id}/cancel", response_model=schemas.ReproductionRunOut)
def cancel(run_id: str, db: DBSession = Depends(get_db)):
    run = _run_or_404(db, run_id)
    if run.status != "running":
        raise HTTPException(409, f"The run has already finished ({run.status.replace('_', ' ')}).")
    run.cancel_requested = True  # the agent stops before its next step
    db.commit()
    return _out(run)


@router.get("/reproduction-runs/{run_id}/screenshot")
def screenshot(run_id: str, db: DBSession = Depends(get_db)):
    run = _run_or_404(db, run_id)
    path = Path(run.screenshot_path or "").resolve()
    if not run.screenshot_path or not path.is_relative_to(runner_api.evidence_root()) or not path.is_file():
        raise HTTPException(404, "No screenshot for this run")
    return FileResponse(path, media_type="image/png")


@router.post("/reproduction-runs/{run_id}/regression-test", response_model=schemas.RegressionTestOut)
def to_regression_test(run_id: str, payload: schemas.RegressionRequest, db: DBSession = Depends(get_db)):
    """The agent's steps, plus a check that the symptom it proved is gone."""
    run = _run_or_404(db, run_id)
    if run.status != "reproduced" or not run.plan or not run.symptom:
        raise HTTPException(409, "Only a run that reproduced the bug has steps to turn into a test.")
    make = REGRESSION_ASSERTION.get(run.symptom["kind"])
    if not make:
        raise HTTPException(409, "This symptom (a URL) has no automatic check yet; write the assertion yourself.")
    bug = db.get(models.Bug, run.bug_id)
    assertion = {**make(run.symptom), "why": f"The agent reproduced: {run.symptom.get('explanation', '')}"}
    plan = {**run.plan, "assertions": [assertion],
            "assumptions": [f"The steps were found by the reproduction agent ({run.ai_provider}/{run.ai_model}); "
                            "check they match how users really get there."]}
    oracle = (f"{ORACLE_NAMES.get(bug.oracle_heuristic, bug.oracle_heuristic)}. {bug.oracle_explanation or ''}".strip()
              if bug.oracle_heuristic else "Not reviewed yet: the check below is the symptom the agent saw.")
    code = render(plan, bug_title=bug.title, oracle=oracle, issue_links=[f"{f.issue_key} {f.issue_url}" for f in bug.filings],
                  provenance=f"reproduction agent, {run.ai_provider}/{run.ai_model}, {agent.PROMPT_VERSION}",
                  known_failing=payload.known_failing)
    test = models.RegressionTest(
        bug_id=bug.id, plan=plan, code=code, filename=f"{slug(bug.title)}.spec.ts", base_url=plan.get("base_url"),
        known_failing=payload.known_failing, warnings=[], ai_provider=run.ai_provider, ai_model=run.ai_model,
        prompt_version=agent.PROMPT_VERSION,
    )
    db.add(test)
    db.commit()
    db.refresh(test)
    return test
