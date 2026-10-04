"""Run every regression test of a project, and export them as a Playwright project.

A test is the latest one generated for each reviewed bug. Replays run one after
another on the runner's background loop, in a headless browser each, and each
test's own last replay is updated too, so the Bugs page agrees with the suite."""
import io
import json
import logging
import re
import zipfile

from sqlalchemy.orm import Session as DBSession

from app import models
from app.database import SessionLocal
from app.regression.replay import replay
from app.timeutil import utcnow

logger = logging.getLogger(__name__)
ACTIVE: set[str] = set()  # suite run ids being worked on in this process


def latest_tests(db: DBSession, project_id: str) -> list[tuple[models.Bug, models.RegressionTest]]:
    """(bug, its latest regression test) for every bug in the project that has one, oldest bug first."""
    rows = (db.query(models.Bug, models.RegressionTest)
            .join(models.Charter, models.Bug.charter_id == models.Charter.id)
            .join(models.RegressionTest, models.RegressionTest.bug_id == models.Bug.id)
            .filter(models.Charter.project_id == project_id)
            .order_by(models.Bug.created_at, models.RegressionTest.created_at).all())
    latest: dict[str, tuple] = {}
    for bug, test in rows:
        latest[bug.id] = (bug, test)  # later tests overwrite earlier ones
    return list(latest.values())


def meaning(outcome: str, known_failing: bool) -> str:
    """What a replay outcome means for the bug, in words."""
    if outcome == "reproduces":
        return "Bug still there" + (" (expected: marked as a known bug)" if known_failing else "")
    if outcome == "passes":
        return ("Now passes: the bug may be fixed. Check it, then remove test.fail() from the test"
                if known_failing else "Passes")
    return {"broken": "A step could not run", "incomplete": "A step was skipped (a secret or a file)",
            "misses_bug": "The steps never reached the bug"}.get(outcome, outcome)


def _save(run_id: str, **fields) -> None:
    with SessionLocal() as db:
        run = db.get(models.RegressionSuiteRun, run_id)
        for k, v in fields.items():
            setattr(run, k, v)
        db.commit()


def _cancelled(run_id: str) -> bool:
    with SessionLocal() as db:
        run = db.get(models.RegressionSuiteRun, run_id)
        return bool(run and run.cancel_requested)


async def run_suite(run_id: str, items: list[dict], *, base_url: str | None, headless: bool,
                    executable_path: str | None) -> None:
    """items: [{"bug_id", "bug_title", "test_id", "known_failing", "plan", "base_url"}]"""
    ACTIVE.add(run_id)
    results: list[dict] = []
    final: dict = {"status": "done"}
    try:
        for item in items:
            if _cancelled(run_id):
                final = {"status": "cancelled", "detail": "Cancelled by the tester."}
                break
            row = {k: item[k] for k in ("bug_id", "bug_title", "test_id", "known_failing")}
            try:
                result = await replay(item["plan"], base_url=base_url or item["base_url"], headless=headless,
                                      executable_path=executable_path)
                failed = next((s for s in result["steps"] if s["status"] in ("failed", "skipped")), None)
                row |= {"outcome": result["outcome"], "duration_ms": result["duration_ms"],
                        "meaning": meaning(result["outcome"], item["known_failing"]),
                        "detail": f"step {failed['n']}: {failed['detail']}" if failed else ""}
                with SessionLocal() as db:
                    test = db.get(models.RegressionTest, item["test_id"])
                    if test:
                        test.replay, test.replayed_at = result, utcnow()
                        db.commit()
            except Exception as err:
                row |= {"outcome": "error", "duration_ms": 0, "meaning": "Could not run",
                        "detail": (str(err).strip().splitlines() or [type(err).__name__])[0][:300]}
            results.append(row)
            _save(run_id, results=list(results))
    except Exception as err:
        logger.exception("Suite run %s failed", run_id)
        final = {"status": "error", "detail": f"The run failed: {err}"}
    finally:
        try:  # saved before it stops counting as active, so a read in between never sees an orphan
            _save(run_id, results=list(results), finished_at=utcnow(), **final)
        finally:
            ACTIVE.discard(run_id)
    if final["status"] == "done":
        await _tell(run_id, results)


def noteworthy(results: list[dict]) -> dict:
    """What in a run is worth a message: tests that broke or fail, and known bugs that now pass."""
    expected = lambda r: (r["outcome"] == "reproduces") if r["known_failing"] else (r["outcome"] == "passes")  # noqa: E731
    fixed = [r for r in results if r["known_failing"] and r["outcome"] == "passes"]
    wrong = [r for r in results if not expected(r) and r not in fixed]
    return {"fixed": fixed, "wrong": wrong, "as_expected": len(results) - len(fixed) - len(wrong)}


async def _tell(run_id: str, results: list[dict]) -> None:
    from app import notify
    n = noteworthy(results)
    if not (n["fixed"] or n["wrong"]):
        return
    with SessionLocal() as db:
        project_id = db.get(models.RegressionSuiteRun, run_id).project_id
    parts = ([f"{len(n['wrong'])} test{'s' if len(n['wrong']) != 1 else ''} failed or broke"] if n["wrong"] else []) + \
            ([f"{len(n['fixed'])} known bug{'s' if len(n['fixed']) != 1 else ''} now pass{'es' if len(n['fixed']) == 1 else ''}"] if n["fixed"] else []) + \
            [f"{n['as_expected']} as expected"]
    detail = "\n".join(f"• {r['bug_title']}: {r['meaning']}" for r in (n["wrong"] + n["fixed"])[:5])
    await notify.send_async(project_id, "suite_problem", "Regression run: " + ", ".join(parts), detail, "/regression")


# ---------- export ----------

PACKAGE = {
    "name": "regression-tests", "private": True, "type": "module",
    "scripts": {"test": "playwright test"},
    "devDependencies": {"@playwright/test": "^1.55.0"},
}
CONFIG = """import { defineConfig } from '@playwright/test'

// Exported from Mirmera. BASE_URL points every test at an environment:
//   BASE_URL=https://staging.example.com npx playwright test
export default defineConfig({
  testDir: './tests',
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { trace: 'retain-on-failure', screenshot: 'only-on-failure' },
})
"""


def export_zip(project: models.Project, pairs: list[tuple[models.Bug, models.RegressionTest]]) -> bytes:
    """A Playwright project with every test: tests/*.spec.ts, config, package.json, README."""
    buf = io.BytesIO()
    used: set[str] = set()
    secrets: set[str] = set()
    rows = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for bug, test in pairs:
            name, n = test.filename, 2
            while name in used:  # two bugs with the same title
                name, n = re.sub(r"(\.spec\.ts)$", f"-{n}\\1", test.filename), n + 1
            used.add(name)
            z.writestr(f"tests/{name}", test.code)
            secrets |= set(re.findall(r"process\.env\.(MIRMERA_SECRET_[A-Z0-9_]+)", test.code))
            last = (test.replay or {}).get("outcome", "not replayed")
            rows.append(f"| `{name}` | {bug.title.replace('|', '/')} | {'yes' if test.known_failing else 'no'} | {last} |")
        z.writestr("playwright.config.ts", CONFIG)
        z.writestr("package.json", json.dumps(PACKAGE, indent=2) + "\n")
        base = project.app_url or next((t.base_url for _, t in pairs if t.base_url), None) or "http://localhost:3000"
        secret_lines = "\n".join(f"- `{s}`" for s in sorted(secrets)) or "- none"
        z.writestr("README.md", f"""# Regression tests: {project.name}

Exported from Mirmera: one test per reviewed bug, generated from what was recorded
in exploratory sessions. Each test asserts what *should* happen (the oracle).

## Run

```
npm install
npx playwright install chromium
BASE_URL={base} npx playwright test
```

On Windows PowerShell: `$env:BASE_URL="{base}"; npx playwright test`

Tests marked as a known bug call `test.fail()`: they pass while the bug exists and
fail the day it is fixed. That's the cue to check the fix and delete the line.

## Secrets

Set these in the environment before running (never commit them):

{secret_lines}

## Tests

| File | Bug | Known bug | Last replay in Mirmera |
|---|---|---|---|
{chr(10).join(rows) or '| (none) | | | |'}

Tests that upload files read them from a `fixtures/` folder next to the test.
""")
    return buf.getvalue()
