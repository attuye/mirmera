"""Regression tests for reviewed bugs: AI plan from the recorded actions,
rendered Playwright code, and a replay that checks the test reproduces the bug."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai import tasks
from app.ai.routing import provider_for
from app.config import settings
from app.database import get_db
from app.evidence_text import _defang as defang
from app.evidence_text import browser_context
from app.regression import steps as bug_steps
from app.regression.plan import MAX_ACTIONS, action_lines, origin, resolve, slug
from app.regression.render import render
from app import test_data
from app.browsers import BrowserMissing
from app.regression.replay import replay as run_replay
from app.routers import runner as runner_api
from app.routers.trackers import _bug_or_404
from app.runner import RunnerUnavailable, manager
from app.timeutil import utcnow
from app.trackers.report import ORACLE_NAMES

router = APIRouter(tags=["regression tests"])


def _latest(db: DBSession, bug_id: str) -> models.RegressionTest | None:
    return (db.query(models.RegressionTest).filter(models.RegressionTest.bug_id == bug_id)
            .order_by(models.RegressionTest.created_at.desc()).first())


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def drop_bug_assertions(assertions: list[dict], actual: str | None) -> tuple[list[dict], list[str]]:
    """A check that expects what the report says ACTUALLY happens asserts the bug: it passes
    while the bug exists and fails once it is fixed. Those are dropped, with the reason."""
    bad_text = _norm(actual)
    kept, notes = [], []
    for a in assertions:
        shown = a.get("expected") if a["kind"] in ("text", "value") else (
            (a.get("locator") or {}).get("value") or (a.get("locator") or {}).get("name") if a["kind"] == "visible" else None)
        s = _norm(shown)
        if bad_text and len(s) >= 6 and (s in bad_text or bad_text in s):
            notes.append(f'Mirmera dropped a check that expected "{shown}", which is what the report says actually '
                         f"happens: it would pass while the bug exists. ({a.get('why', '')})".replace(" ()", ""))
        else:
            kept.append(a)
    return kept, notes


def skipped_actions(plan: schemas.LLMRegressionPlan, actions: list[models.Evidence]) -> list[str]:
    """Recorded actions the AI left out between the first and the last action it kept."""
    used = sorted({s.action_index for s in plan.steps if s.kind == "recorded" and 0 <= (s.action_index or 0) < len(actions)})
    if len(used) < 2:
        return []
    missing = [i for i in range(used[0], used[-1] + 1) if i not in used]
    if not missing:
        return []
    shown = "; ".join(actions[i].summary for i in missing[:6]) + (f"; and {len(missing) - 6} more" if len(missing) > 6 else "")
    return [f"The AI left out {len(missing)} recorded action(s) between the steps it kept: {shown}. "
            "If the test does not reach the bug, edit the steps and add them back."]


def _report(bug: models.Bug) -> dict:
    return {"title": bug.title, "steps": bug.steps_to_reproduce or [], "expected": bug.expected,
            "actual": bug.actual, "oracle": bug.oracle_heuristic, "why_its_a_problem": bug.oracle_explanation,
            "severity": bug.severity}


def _steps_out(db: DBSession, bug: models.Bug) -> schemas.BugStepsOut:
    st, rec = bug_steps.steps_for(db, bug)
    return schemas.BugStepsOut(
        steps=[schemas.BugStep(**s, summary=bug_steps.describe_step(s)) if s["type"] == "goto"
               else schemas.BugStep(type="action", url=s.get("url"), action=s["action"], summary=bug_steps.describe_step(s))
               for s in st.steps],
        source=st.source, note=st.note, anchor=st.anchor, recorded_actions=len(rec.actions))


@router.get("/bugs/{bug_id}/steps", response_model=schemas.BugStepsOut)
def get_steps(bug_id: str, db: DBSession = Depends(get_db)):
    """The steps to this bug: edited by the tester, picked by Mirmera from the recording,
    or (when Mirmera can't tell) the recorded actions the AI will pick from."""
    bug = _bug_or_404(db, bug_id)
    runner_api.sync_signals(db, bug.session_id)
    return _steps_out(db, bug)


@router.put("/bugs/{bug_id}/steps", response_model=schemas.BugStepsOut)
def save_steps(bug_id: str, payload: schemas.BugStepsUpdate, db: DBSession = Depends(get_db)):
    """Save the tester's steps; the regression test and the agent use them as they are."""
    bug = _bug_or_404(db, bug_id)
    try:
        bug.edited_steps = bug_steps.clean_steps(payload.steps)
    except bug_steps.StepError as err:
        raise HTTPException(422, str(err)) from err
    db.commit()
    return _steps_out(db, bug)


@router.delete("/bugs/{bug_id}/steps", response_model=schemas.BugStepsOut)
def reset_steps(bug_id: str, db: DBSession = Depends(get_db)):
    """Forget the tester's edits and go back to the steps from the recording."""
    bug = _bug_or_404(db, bug_id)
    bug.edited_steps = None
    db.commit()
    return _steps_out(db, bug)


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
    st, rec = bug_steps.steps_for(db, bug)
    if not st.steps:
        raise HTTPException(409, "Nothing was recorded for this bug. Regression tests are built from the recorded "
                                 "browser: open it on the session screen and reproduce the bug there, or add the "
                                 "steps yourself under Steps to the bug.")
    session = bug.session
    project = db.get(models.Project, bug.charter.project_id)
    provider = provider_for(project, "regression", payload.provider)
    evidence = [e for e in runner_api.evidence_for_bug(db, bug) if e.kind != "action"]
    browser = browser_context(evidence, session.started_at, used=True)
    warnings: list[str] = []

    if st.fixed:  # the steps are known: the AI writes only the checks
        checks = await tasks.write_regression_checks(
            provider, report=_report(bug), browser=browser,
            steps=[f"{i}. {defang(bug_steps.describe_step(s))}" for i, s in enumerate(st.steps, 1)])
        first = st.steps[0]["url"]
        resolved = {"title": checks.test_title, "base_url": origin(first), "steps": st.steps,
                    "assertions": [a.model_dump(exclude_none=True) for a in checks.assertions],
                    "assumptions": checks.assumptions}
        prompt_version = tasks.PROMPT_VERSIONS["regression_checks"]
    else:  # no anchor in the recording: the AI picks steps from this bug's own actions
        actions = rec.actions
        if len(actions) > MAX_ACTIONS:
            warnings.append(f"Only the last {MAX_ACTIONS} of {len(actions)} recorded actions were considered.")
            actions = actions[-MAX_ACTIONS:]
        plan = await tasks.plan_regression_test(
            provider, report=_report(bug), pages=rec.pages, browser=browser,
            actions=[defang(line) for line in action_lines(actions, session.started_at)])
        resolved, plan_warnings = resolve(plan, actions, rec.pages, origin(rec.pages[0]) if rec.pages else None)
        warnings += plan_warnings + skipped_actions(plan, actions)
        prompt_version = tasks.PROMPT_VERSIONS["regression_test"]

    kept, dropped = drop_bug_assertions(resolved["assertions"], bug.actual)
    if not kept:
        raise HTTPException(502, "Every check the AI wrote expects what the report says actually happens, so the test "
                                 "would pass while the bug exists. Try again, or use a stronger AI provider.")
    resolved["assertions"] = kept
    resolved["assumptions"] = [*resolved["assumptions"], *dropped]
    if st.reach:
        resolved["reach"] = {"url_contains": st.reach, "recorded": st.anchor}
    env = bug.environment or {}
    if env.get("engine") and env["engine"] != "chromium":  # found in Firefox or WebKit: the test runs there
        resolved["browser"] = env["engine"]
    if env.get("clock_at"):  # found with the browser's clock set: the test sets it too
        resolved["clock"] = {"at": env["clock_at"], "timezone": env.get("timezone") or "Africa/Addis_Ababa"}
    base_url = resolved["base_url"]
    issue_links = [f"{f.issue_key} {f.issue_url}" for f in bug.filings]
    oracle = f"{ORACLE_NAMES.get(bug.oracle_heuristic, bug.oracle_heuristic)}. {bug.oracle_explanation or ''}".strip()
    code = render(resolved, bug_title=bug.title, oracle=oracle, issue_links=issue_links,
                  provenance=f"{provider.name}/{provider.model}, {prompt_version}",
                  known_failing=payload.known_failing)

    test = models.RegressionTest(
        bug_id=bug.id, plan=resolved, code=code, filename=f"{slug(bug.title)}.spec.ts", base_url=base_url,
        known_failing=payload.known_failing, warnings=warnings,
        ai_provider=provider.name, ai_model=provider.model, prompt_version=prompt_version,
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
                       executable_path=settings.runner_chromium_path, browser=payload.browser,
                       data=test_data.for_run(db, db.get(models.Bug, test.bug_id).charter.project_id)), timeout=240)
    except ImportError as err:
        raise RunnerUnavailable("Playwright is not installed. Run: python -m playwright install chromium") from err
    except BrowserMissing as err:
        raise RunnerUnavailable(str(err)) from err
    except Exception as err:
        if "Executable doesn't exist" in str(err) or "executable" in str(err).lower():
            raise RunnerUnavailable(f"The browser would not start: {str(err).splitlines()[0]}") from err
        raise
    test.replay = result
    test.replayed_at = utcnow()
    db.commit()
    db.refresh(test)
    return test


@router.post("/regression-tests/{test_id}/fresh-data", response_model=schemas.FreshDataOut)
def fresh_data(test_id: str, db: DBSession = Depends(get_db)):
    """The app refused the test's data ("already registered"): the steps it is about get fresh values
    ({email}, {phone}, "… {unique}"), the spec is written again, and the test is replayed."""
    test = db.get(models.RegressionTest, test_id)
    if not test:
        raise HTTPException(404, "Regression test not found")
    page = (test.replay or {}).get("page") or {}
    said = " ".join(page.get("messages") or []) + " " + " ".join((test.replay or {}).get("signals", {}).get("failed") or [])
    plan, changes = test_data.freshen(test.plan, said)
    if not changes:
        raise HTTPException(409, "No step fills a value Mirmera can make fresh (an email, a phone, a name): "
                                 "edit the test's steps, or add the data in Settings → Test data.")
    bug = db.get(models.Bug, test.bug_id)
    issue_links = [f"{f.issue_key} {f.issue_url}" for f in bug.filings]
    oracle = f"{ORACLE_NAMES.get(bug.oracle_heuristic, bug.oracle_heuristic)}. {bug.oracle_explanation or ''}".strip()
    test.plan = plan
    test.code = render(plan, bug_title=bug.title, oracle=oracle, issue_links=issue_links,
                       provenance=f"{test.ai_provider}/{test.ai_model}, {test.prompt_version}", known_failing=test.known_failing)
    test.warnings = list(test.warnings or []) + ["Fresh values each run: " + ", ".join(f'{c["field"]} ({c["now"]})' for c in changes)]
    db.commit()
    replayed = replay(test_id, schemas.ReplayRequest(), db)
    return schemas.FreshDataOut(test=replayed, changes=changes)


@router.get("/regression-tests/{test_id}/download")
def download(test_id: str, db: DBSession = Depends(get_db)):
    test = db.get(models.RegressionTest, test_id)
    if not test:
        raise HTTPException(404, "Regression test not found")
    return Response(test.code, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{test.filename}"'})
