"""Deleting projects, sessions and bugs, with everything that belongs to them.

SQLite doesn't enforce foreign keys here, so children are deleted explicitly,
deepest first, and files recorded for a session are removed from disk. Each
function returns what it deleted, for the confirmation message. Nothing that is
being worked on (a recorded browser, the reproduction agent, an isolation run)
can be deleted: BusyError says what to stop first.
"""
import shutil
from collections import Counter

from sqlalchemy.orm import Session as DBSession

from app import models, stories
from app.agent import reproduce as agent
from app.regression import isolate
from app.routers.runner import session_folder
from app.runner import manager


class BusyError(Exception):
    pass


def _check_bugs_idle(db: DBSession, bug_ids: list[str]) -> None:
    if not bug_ids:
        return
    runs = db.query(models.ReproductionRun.id).filter(models.ReproductionRun.bug_id.in_(bug_ids),
                                                      models.ReproductionRun.status == "running").all()
    if any(r.id in agent.ACTIVE for r in runs):
        raise BusyError("The reproduction agent is working on a bug here. Cancel it or let it finish first.")
    runs = db.query(models.IsolationRun.id).filter(models.IsolationRun.bug_id.in_(bug_ids),
                                                   models.IsolationRun.status == "running").all()
    if any(r.id in isolate.ACTIVE for r in runs):
        raise BusyError("A bug here is being isolated. Cancel it or let it finish first.")
    from app.regression import retest
    runs = db.query(models.Retest.id).filter(models.Retest.bug_id.in_(bug_ids), models.Retest.outcome == "running").all()
    if any(r.id in retest.ACTIVE for r in runs):
        raise BusyError("A bug here is being retested after its issue closed. Let it finish first.")


def _check_sessions_idle(session_ids: list[str]) -> None:
    for sid in session_ids:
        if manager.get(sid) is not None:  # a runner exists only while its browser is open
            raise BusyError("The recorded browser is still open for a session here. Stop it first.")


def _delete_bugs(db: DBSession, bug_ids: list[str], counts: Counter) -> None:
    if not bug_ids:
        return
    retests = db.query(models.Retest).filter(models.Retest.bug_id.in_(bug_ids)).delete(synchronize_session=False)
    if retests:  # only listed when there were any
        counts["retests"] += retests
    for model in (models.RegressionTest, models.ReproductionRun, models.IsolationRun, models.BugFiling):
        counts[model.__tablename__] += db.query(model).filter(model.bug_id.in_(bug_ids)).delete(synchronize_session=False)
    # Screenshots taken when a bug was logged stay in the session's evidence.
    db.query(models.Evidence).filter(models.Evidence.bug_id.in_(bug_ids)).update(
        {models.Evidence.bug_id: None}, synchronize_session=False)
    counts["bugs"] += db.query(models.Bug).filter(models.Bug.id.in_(bug_ids)).delete(synchronize_session=False)


def _delete_sessions(db: DBSession, session_ids: list[str], counts: Counter) -> None:
    if not session_ids:
        return
    bug_ids = [b.id for b in db.query(models.Bug.id).filter(models.Bug.session_id.in_(session_ids))]
    _delete_bugs(db, bug_ids, counts)
    for model in (models.Note, models.Debrief, models.Evidence):
        counts[model.__tablename__] += db.query(model).filter(model.session_id.in_(session_ids)).delete(synchronize_session=False)
    # Risk re-scores the tester decided on changed the risk: they stay in its history,
    # without the session. Undecided suggestions from the session go with it.
    q = db.query(models.RiskChange).filter(models.RiskChange.session_id.in_(session_ids))
    counts["risk_changes"] += q.filter(models.RiskChange.status.in_(("proposed", "no_change"))).delete(synchronize_session=False)
    q.filter(models.RiskChange.status.in_(("accepted", "rejected"))).update(
        {models.RiskChange.session_id: None}, synchronize_session=False)
    counts["sessions"] += db.query(models.Session).filter(models.Session.id.in_(session_ids)).delete(synchronize_session=False)


def _remove_files(session_ids: list[str]) -> None:
    for sid in session_ids:
        shutil.rmtree(session_folder(sid), ignore_errors=True)


def delete_bug(db: DBSession, bug: models.Bug) -> dict:
    _check_bugs_idle(db, [bug.id])
    counts: Counter = Counter()
    _delete_bugs(db, [bug.id], counts)
    db.commit()
    return dict(counts)


def delete_session(db: DBSession, session: models.Session) -> dict:
    sid = session.id  # the object expires once the row is gone
    bug_ids = [b.id for b in db.query(models.Bug.id).filter(models.Bug.session_id == sid)]
    _check_sessions_idle([sid])
    _check_bugs_idle(db, bug_ids)
    counts: Counter = Counter()
    _delete_sessions(db, [sid], counts)
    db.commit()
    _remove_files([sid])
    return dict(counts)


def delete_project(db: DBSession, project: models.Project) -> dict:
    pid = project.id  # the object expires once the row is gone
    charter_ids = [c.id for c in db.query(models.Charter.id).filter(models.Charter.project_id == pid)]
    session_ids = [s.id for s in db.query(models.Session.id).filter(models.Session.charter_id.in_(charter_ids))] if charter_ids else []
    bug_ids = [b.id for b in db.query(models.Bug.id).filter(models.Bug.session_id.in_(session_ids))] if session_ids else []
    _check_sessions_idle(session_ids)
    _check_bugs_idle(db, bug_ids)
    from app.regression import suite
    running = db.query(models.RegressionSuiteRun.id).filter(models.RegressionSuiteRun.project_id == pid,
                                                           models.RegressionSuiteRun.status == "running").all()
    if any(r.id in suite.ACTIVE for r in running):
        raise BusyError("The regression tests are running for this project. Cancel the run or let it finish first.")
    counts: Counter = Counter()
    _delete_sessions(db, session_ids, counts)
    counts["suite_runs"] += db.query(models.RegressionSuiteRun).filter(
        models.RegressionSuiteRun.project_id == pid).delete(synchronize_session=False)
    retests = db.query(models.Retest).filter(models.Retest.project_id == pid).delete(synchronize_session=False)
    if retests:
        counts["retests"] += retests
    logs = db.query(models.NotificationLog).filter(models.NotificationLog.project_id == pid).delete(synchronize_session=False)
    if logs:
        counts["notification_logs"] += logs
    summaries = db.query(models.ReportSummary).filter(models.ReportSummary.project_id == pid).delete(synchronize_session=False)
    if summaries:
        counts["report_summaries"] += summaries
    risk_ids = [r.id for r in db.query(models.Risk.id).filter(models.Risk.project_id == pid)]
    story_ids = [st.id for st in db.query(models.Story.id).filter(models.Story.project_id == pid)]
    stories.unlink_all(db, story_ids=story_ids, risk_ids=risk_ids, charter_ids=charter_ids)
    if story_ids:
        counts["stories"] += db.query(models.Story).filter(models.Story.project_id == pid).delete(synchronize_session=False)
    sprints = db.query(models.Sprint).filter(models.Sprint.project_id == pid).delete(synchronize_session=False)
    if sprints:
        counts["sprints"] += sprints
    if risk_ids:
        counts["risk_changes"] += db.query(models.RiskChange).filter(models.RiskChange.risk_id.in_(risk_ids)).delete(synchronize_session=False)
    if charter_ids or risk_ids:
        db.execute(models.charter_risks.delete().where(
            models.charter_risks.c.charter_id.in_(charter_ids) | models.charter_risks.c.risk_id.in_(risk_ids)))
    for model in (models.Charter, models.Risk, models.ProductModel, models.Artifact):
        counts[model.__tablename__] += db.query(model).filter(model.project_id == pid).delete(synchronize_session=False)
    db.query(models.Project).filter(models.Project.id == pid).delete(synchronize_session=False)
    db.commit()
    _remove_files(session_ids)
    return dict(counts)
