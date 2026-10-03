"""Regression tests for reviewed bugs: AI plan from the recorded actions,
rendered Playwright code, and a replay that checks the test reproduces the bug."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai import tasks
from app.ai.base import get_provider
from app.config import settings
from app.database import get_db
from app.evidence_text import _defang as defang
from app.evidence_text import browser_context
from app.regression.plan import MAX_ACTIONS, action_lines, origin, resolve, slug
from app.regression.render import render
from app.regression.replay import replay as run_replay
from app.routers import runner as runner_api
from app.routers.trackers import _bug_or_404
from app.runner import RunnerUnavailable, manager
from app.timeutil import utcnow
from app.trackers.report import ORACLE_NAMES

router = APIRouter(tags=["regression tests"])
BUG_GRACE_SECONDS = 5  # actions recorded just after logging the bug still count


def _latest(db: DBSession, bug_id: str) -> models.RegressionTest | None:
    return (db.query(models.RegressionTest).filter(models.RegressionTest.bug_id == bug_id)
            .order_by(models.RegressionTest.created_at.desc()).first())


def _recorded_before(db: DBSession, bug: models.Bug, kind: str) -> list[models.Evidence]:
    from datetime import timedelta
    return (db.query(models.Evidence)
            .filter(models.Evidence.session_id == bug.session_id, models.Evidence.kind == kind,
                    models.Evidence.captured_at <= bug.created_at + timedelta(seconds=BUG_GRACE_SECONDS))
            .order_by(models.Evidence.captured_at).all())


@router.get("/bugs/{bug_id}/regression-test", response_model=schemas.RegressionTestOut | None)
def get_regression_test(bug_id: str, db: DBSession = Depends(get_db)):
    _bug_or_404(db, bug_id)
    return _latest(db, bug_id)


@router.post("/bugs/{bug_id}/regression-test", response_model=schemas.RegressionTestOut)
async def generate(bug_id: str, payload: schemas.RegressionRequest, db: DBSession = Depends(get_db)):
    bug = _bug_or_404(db, bug_id)
    if bug.kind != "bug":
        raise HTTPException(409, "Only a reviewed bug can get a regression test: it needs the oracle that says what "
                                 "should happen. Review it as a bug first (a question has no oracle yet).")
    runner_api.sync_signals(db, bug.session_id)
    actions = _recorded_before(db, bug, "action")
    pages = list(dict.fromkeys(e.url for e in _recorded_before(db, bug, "navigation") if e.url))
    if not actions and not pages:
        raise HTTPException(409, "Nothing was recorded for this bug. Regression tests are built from the recorded "
                                 "browser: open it on the session screen and reproduce the bug there.")
    warnings = []
    if len(actions) > MAX_ACTIONS:
        warnings.append(f"Only the last {MAX_ACTIONS} of {len(actions)} recorded actions were considered.")
        actions = actions[-MAX_ACTIONS:]

    session = bug.session
    project = db.get(models.Project, bug.charter.project_id)
    provider = get_provider(payload.provider or project.default_ai_provider)
    evidence = [e for e in runner_api.evidence_for_bug(db, bug) if e.kind != "action"]
    plan = await tasks.plan_regression_test(
        provider,
        report={"title": bug.title, "steps": bug.steps_to_reproduce or [], "expected": bug.expected,
                "actual": bug.actual, "oracle": bug.oracle_heuristic, "why_its_a_problem": bug.oracle_explanation,
                "severity": bug.severity},
        actions=[defang(line) for line in action_lines(actions, session.started_at)],
        pages=pages,
        browser=browser_context(evidence, session.started_at, used=True),
    )
    base_url = origin(pages[0]) if pages else None
    resolved, plan_warnings = resolve(plan, actions, pages, base_url)
    issue_links = [f"{f.issue_key} {f.issue_url}" for f in bug.filings]
    oracle = f"{ORACLE_NAMES.get(bug.oracle_heuristic, bug.oracle_heuristic)}. {bug.oracle_explanation or ''}".strip()
    code = render(resolved, bug_title=bug.title, oracle=oracle, issue_links=issue_links,
                  provenance=f"{provider.name}/{provider.model}, {tasks.PROMPT_VERSIONS['regression_test']}",
                  known_failing=payload.known_failing)

    test = models.RegressionTest(
        bug_id=bug.id, plan=resolved, code=code, filename=f"{slug(bug.title)}.spec.ts", base_url=base_url,
        known_failing=payload.known_failing, warnings=warnings + plan_warnings,
        ai_provider=provider.name, ai_model=provider.model, prompt_version=tasks.PROMPT_VERSIONS["regression_test"],
    )
    db.add(test)
    db.commit()
    db.refresh(test)
    return test


@router.post("/regression-tests/{test_id}/replay", response_model=schemas.RegressionTestOut)
def replay(test_id: str, payload: schemas.ReplayRequest, db: DBSession = Depends(get_db)):
    """Run the test's plan headless against the app (or another base URL, e.g. staging)."""
    test = db.get(models.RegressionTest, test_id)
    if not test:
        raise HTTPException(404, "Regression test not found")
    try:
        result = manager.loop.run(
            run_replay(test.plan, base_url=payload.base_url or test.base_url, headless=True,
                       executable_path=settings.runner_chromium_path), timeout=240)
    except ImportError as err:
        raise RunnerUnavailable("Playwright is not installed. Run: python -m playwright install chromium") from err
    except Exception as err:
        if "Executable doesn't exist" in str(err) or "executable" in str(err).lower():
            raise RunnerUnavailable(f"The browser would not start: {str(err).splitlines()[0]}") from err
        raise
    test.replay = result
    test.replayed_at = utcnow()
    db.commit()
    db.refresh(test)
    return test


@router.get("/regression-tests/{test_id}/download")
def download(test_id: str, db: DBSession = Depends(get_db)):
    test = db.get(models.RegressionTest, test_id)
    if not test:
        raise HTTPException(404, "Regression test not found")
    return Response(test.code, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{test.filename}"'})
