"""Retest a bug when its issue is closed, and say on the issue what happened.

Trackers can't reach a laptop, so Mirmera asks them: "Check closed issues" (or a
timer, RETEST_POLL_MINUTES) looks up every issue filed from the project. When
one has gone from open to closed since the last look, the bug's latest
regression test is replayed and the result is posted as a comment on the issue:
the fix is verified, the bug is still there (with what failed), or the test
couldn't tell. An issue reopened and closed again is retested again.
"""
import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session as DBSession

from app import models
from app.database import SessionLocal
from app.regression.replay import replay
from app.regression.suite import latest_tests, meaning
from app.timeutil import utcnow
from app.trackers import TrackerError, get_tracker
from app.trackers.report import Block

logger = logging.getLogger(__name__)
ACTIVE: set[str] = set()  # retest ids being replayed in this process
_LOCKS: dict[int, asyncio.Lock] = {}  # one check at a time per event loop, so a close is retested once


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    return _LOCKS.setdefault(id(loop), asyncio.Lock())

VERDICT = {"reproduces": "still_fails", "passes": "fixed"}


@dataclass
class CheckResult:
    checked: int = 0
    retests: list[models.Retest] = field(default_factory=list)  # one per newly closed issue
    problems: list[str] = field(default_factory=list)  # issues that couldn't be looked up
    to_replay: list[str] = field(default_factory=list)  # retest ids waiting for a replay


def filings_of(db: DBSession, project_id: str) -> list[models.BugFiling]:
    return (db.query(models.BugFiling)
            .join(models.Bug, models.BugFiling.bug_id == models.Bug.id)
            .join(models.Charter, models.Bug.charter_id == models.Charter.id)
            .filter(models.Charter.project_id == project_id)
            .order_by(models.BugFiling.created_at).all())


async def check_project(db: DBSession, project: models.Project) -> CheckResult:
    """Look up every filed issue; start a retest for each one closed since the last look."""
    async with _lock():
        result = CheckResult()
        tests = {bug.id: test for bug, test in latest_tests(db, project.id)}
        trackers: dict[tuple[str, str], object] = {}
        for filing in filings_of(db, project.id):
            where = (filing.tracker, filing.target)
            try:
                if where not in trackers:
                    trackers[where] = get_tracker(filing.tracker, project, target=filing.target)
                issue = await trackers[where].issue(filing.issue_key)
            except TrackerError as err:
                result.problems.append(f"{filing.issue_key} ({filing.tracker}): {err}")
                continue
            result.checked += 1
            newly_closed = issue.closed and not filing.issue_closed
            filing.issue_closed, filing.issue_status, filing.status_checked_at = issue.closed, issue.status, utcnow()
            if not newly_closed:
                continue
            test = tests.get(filing.bug_id)
            retest = models.Retest(
                project_id=project.id, bug_id=filing.bug_id, filing_id=filing.id, tracker=filing.tracker,
                issue_key=filing.issue_key, issue_url=filing.issue_url, test_id=test.id if test else None,
                outcome="running" if test else "no_test",
                detail=None if test else "The bug has no regression test, so the fix couldn't be checked. "
                                         "Generate one on the Bugs page and replay it.",
                finished_at=None if test else utcnow())
            db.add(retest)
            result.retests.append(retest)
        db.commit()
        for r in result.retests:
            db.refresh(r)
            if r.outcome == "running":
                result.to_replay.append(r.id)
                ACTIVE.add(r.id)  # marked before the replay starts, so it never looks interrupted
        return result


# ---------- the comment ----------

def comment_blocks(outcome: str, replay_result: dict | None, test_name: str, known_failing: bool, note: str) -> list[Block]:
    base = (replay_result or {}).get("base_url") or "the recorded address"
    intro = f"Mirmera replayed the regression test `{test_name}` on {base} after this issue was closed."
    if outcome == "fixed":
        checks = [f"{c['why'] or c['kind']}: passed" for c in replay_result["assertions"]]
        return [Block("paragraph", f"{intro} It passes: the fix is verified."),
                *([Block("bullets", items=checks)] if checks else []),
                *([Block("paragraph", "The test was marked as a known bug (test.fail()); "
                                      "that mark can now be removed.")] if known_failing else [])]
    if outcome == "still_fails":
        failed = [f"{c['why'] or c['kind']}: {c['detail']}" for c in replay_result["assertions"] if c["status"] == "failed"]
        steps = [s["description"] for s in replay_result["steps"]]
        return [Block("paragraph", f"{intro} The bug is still there, so this may need reopening."),
                Block("heading", "What still fails"), Block("bullets", items=failed or ["(no detail)"]),
                Block("heading", "Steps replayed"), Block("numbered", items=steps)]
    return [Block("paragraph", f"{intro} It couldn't tell whether the bug is fixed: {note}. "
                               "Someone should check by hand.")]


def _note(result: dict) -> str:
    failed = next((s for s in result["steps"] if s["status"] in ("failed", "skipped")), None)
    words = meaning(result["outcome"], False)
    return f"{words} (step {failed['n']}: {failed['detail']})" if failed else words


async def _one(retest_id: str, base_url: str | None, headless: bool, executable_path: str | None) -> None:
    with SessionLocal() as db:
        r = db.get(models.Retest, retest_id)
        test = db.get(models.RegressionTest, r.test_id)
        project = db.get(models.Project, r.project_id)
        filing = db.get(models.BugFiling, r.filing_id)
        plan, own_base, name, known = test.plan, test.base_url, test.filename, test.known_failing
    fields: dict = {}
    try:
        result = await replay(plan, base_url=base_url or own_base, headless=headless, executable_path=executable_path)
        outcome = VERDICT.get(result["outcome"], "could_not_check")
        fields = {"replay": result, "outcome": outcome, "detail": None if outcome != "could_not_check" else _note(result)}
        with SessionLocal() as db:  # the test's own last replay agrees with the retest
            t = db.get(models.RegressionTest, test.id)
            t.replay, t.replayed_at = result, utcnow()
            db.commit()
    except Exception as err:
        result = None
        first = (str(err).strip().splitlines() or [type(err).__name__])[0][:300]
        fields = {"outcome": "could_not_check", "detail": f"The replay could not run: {first}"}
    try:
        tracker = get_tracker(filing.tracker, project, target=filing.target)
        await tracker.comment(r.issue_key, comment_blocks(fields["outcome"], result, name, known, fields.get("detail") or ""))
        fields["commented"] = True
    except TrackerError as err:
        fields["comment_error"] = str(err)
    _save(retest_id, finished_at=utcnow(), **fields)


def _save(retest_id: str, **fields) -> None:
    with SessionLocal() as db:
        r = db.get(models.Retest, retest_id)
        if r:
            for k, v in fields.items():
                setattr(r, k, v)
            db.commit()


async def run_retests(retest_ids: list[str], *, base_url: str | None, headless: bool = True,
                      executable_path: str | None = None) -> None:
    """Replays one after another on the runner's loop; each saves and comments as it finishes."""
    for rid in retest_ids:
        ACTIVE.add(rid)
    for rid in retest_ids:
        try:
            await _one(rid, base_url, headless, executable_path)
        except Exception as err:
            logger.exception("Retest %s failed", rid)
            _save(rid, outcome="could_not_check", detail=f"The retest failed: {err}", finished_at=utcnow())
        finally:
            ACTIVE.discard(rid)


def settle_orphans(db: DBSession, retests: list[models.Retest]) -> None:
    """A retest left running by a backend restart is marked as interrupted."""
    changed = False
    for r in retests:
        if r.outcome == "running" and r.id not in ACTIVE:
            r.outcome, r.detail, r.finished_at = "could_not_check", "Interrupted: the backend restarted during the replay.", utcnow()
            changed = True
    if changed:
        db.commit()
