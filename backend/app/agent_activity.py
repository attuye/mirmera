"""What Mirmera's agents are doing, for the progress shown in the portal.

Two kinds of work, shown the same way:
- a Jira work item planned by the Mirmera agent (the panel button or an assignment):
  read the work item → model it → propose risks → draft charters → explore → done;
- an exploration started in Mirmera ("Let Mirmera explore" on a charter): explore → done.

Each item says which stage is running, how far exploring has got (steps used of the
budget, the last few steps with the agent's thoughts) and what it has left behind
(the story, the session, bug drafts).
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session as DBSession

from app import models
from app.timeutil import utcnow

STAGES = [("read", "Read the work item"), ("model", "Model the story"), ("risks", "Propose risks"),
          ("charters", "Draft charters"), ("explore", "Explore in the app"), ("done", "Done")]
ORDER = [k for k, _ in STAGES]
RECENT_STEPS = 6
NOT_ACTIONS = ("note", "problem", "done", "invalid")


STALE = timedelta(minutes=20)  # a task that hasn't moved this long was cut off (the backend restarted)
INTERRUPTED = "Interrupted: the backend stopped while Mirmera was working. Start it again from Jira."


def _state_of_task(t: models.JiraAgentTask) -> str:
    if t.state in ("TASK_STATE_SUBMITTED", "TASK_STATE_WORKING") and utcnow() - (t.updated_at or t.created_at) > STALE:
        return "failed"
    return {"TASK_STATE_SUBMITTED": "running", "TASK_STATE_WORKING": "running", "TASK_STATE_COMPLETED": "done",
            "TASK_STATE_FAILED": "failed", "TASK_STATE_REJECTED": "failed", "TASK_STATE_CANCELED": "cancelled"}.get(t.state, t.state)


def _state_of_run(r: models.ExplorationRun) -> str:
    from app.agent import explore
    if r.status == "running" and r.id not in explore.ACTIVE:
        return "failed"
    return {"finished": "done", "error": "failed"}.get(r.status, r.status)


def exploration(run: models.ExplorationRun | None, db: DBSession) -> dict | None:
    if not run:
        return None
    steps = list(run.steps or [])
    bugs = [db.get(models.Bug, b) for b in run.bug_ids or []]
    return {
        "run_id": run.id, "status": run.status, "charter_id": run.charter_id, "mission": run.charter.mission_statement,
        "session_id": run.session_id, "steps_used": len(steps), "max_steps": run.max_steps,
        "actions": sum(1 for s in steps if s.get("kind") not in NOT_ACTIONS),
        "recent": [{k: s.get(k) for k in ("n", "thought", "action", "kind", "status", "detail")} for s in steps[-RECENT_STEPS:]],
        "bugs": [{"id": b.id, "title": b.title, "severity": b.severity} for b in bugs if b],
        "summary": run.summary, "detail": run.detail,
    }


def _stages(current: str | None, state: str, explored: bool, explore_only: bool = False) -> list[dict]:
    keys = ["explore", "done"] if explore_only else ORDER
    if state == "done":
        at = len(keys)  # all done
    else:
        at = keys.index(current) if current in keys else 0
    out = []
    for i, key in enumerate(keys):
        label = dict(STAGES)[key]
        if key == "explore" and not explored and state == "done":
            status = "skipped"
        elif i < at or (state == "done"):
            status = "done"
        elif i == at:
            status = "active" if state == "running" else state  # failed | cancelled stop here
        else:
            status = "todo"
        out.append({"key": key, "label": label, "status": status})
    return out


def from_task(db: DBSession, t: models.JiraAgentTask) -> dict:
    state = _state_of_task(t)
    run = db.get(models.ExplorationRun, t.exploration_run_id) if t.exploration_run_id else None
    return {
        "id": t.id, "kind": "jira", "state": state, "stage": t.stage or "read",
        "title": t.issue_title or t.issue_key or "A Jira work item", "issue_key": t.issue_key,
        "project_id": t.project_id, "story_id": t.story_id, "started_by": f"Jira {t.issue_key}" if t.issue_key else "Jira",
        "stages": _stages(t.stage or "read", state, explored=run is not None),
        "exploration": exploration(run, db),
        "message": (INTERRUPTED if t.state in ("TASK_STATE_SUBMITTED", "TASK_STATE_WORKING") else t.message)
                   if state in ("failed", "cancelled") else None,
        "started_at": t.created_at, "finished_at": t.updated_at if state != "running" else None,
    }


def from_run(db: DBSession, r: models.ExplorationRun) -> dict:
    state = _state_of_run(r)
    story = r.charter.stories[0] if r.charter.stories else None
    return {
        "id": r.id, "kind": "explore", "state": state, "stage": "explore" if state == "running" else "done",
        "title": r.charter.mission_statement, "issue_key": story.key if story else None,
        "project_id": r.project_id, "story_id": story.id if story else None, "started_by": r.started_by,
        "stages": _stages("explore", state, explored=True, explore_only=True),
        "exploration": exploration(r, db),
        "message": (r.detail if r.status != "running" else INTERRUPTED) if state in ("failed", "cancelled") else None,
        "started_at": r.created_at, "finished_at": r.finished_at,
    }


def activity(db: DBSession, project_ids: list[str], *, story_id: str | None = None, recent_minutes: int | None = None,
             limit: int = 20) -> list[dict]:
    """Running work first, then the latest finished. Explorations that belong to a Jira
    task are shown inside it, not twice."""
    if not project_ids:
        return []
    tasks = (db.query(models.JiraAgentTask)
             .filter(models.JiraAgentTask.project_id.in_(project_ids), models.JiraAgentTask.action == "plan")
             .order_by(models.JiraAgentTask.created_at.desc()).limit(limit).all())
    inside = {t.exploration_run_id for t in tasks if t.exploration_run_id}
    runs = (db.query(models.ExplorationRun).filter(models.ExplorationRun.project_id.in_(project_ids))
            .order_by(models.ExplorationRun.created_at.desc()).limit(limit).all())
    items = [from_task(db, t) for t in tasks] + [from_run(db, r) for r in runs if r.id not in inside]
    if story_id:
        story = db.get(models.Story, story_id)
        charters = {c.id for c in story.charters} if story else set()
        items = [i for i in items if i["story_id"] == story_id
                 or (i["exploration"] and i["exploration"]["charter_id"] in charters)]
    if recent_minutes is not None:
        since = utcnow() - timedelta(minutes=recent_minutes)
        items = [i for i in items if i["state"] == "running" or (i["finished_at"] or datetime.min) >= since]
    items.sort(key=lambda i: (i["state"] != "running", -(i["started_at"].timestamp())))
    return items[:limit]
