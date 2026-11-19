"""User stories: what a story claims, the risks and charters linked to it, the testing
done under those charters, and what is still left.

Rapid Software Testing treats a story as a claim about a change, one oracle among
several, not as the thing being tested. So a story links to risks and charters but
doesn't own them, and "what's left" never says "tested": it lists what was done and
what hasn't been looked at, and the tester decides.
"""
import re
from collections import Counter
from datetime import datetime

from sqlalchemy.orm import Session as DBSession

from app import models
from app.coverage import page_key
from app.timeutil import utcnow
from app.project_report import tested_seconds
from app.trackers import TrackerError, get_tracker

PAGES_SHOWN = 15
UNFULFILLED = {"partially_fulfilled": "partly fulfilled", "not_fulfilled": "not fulfilled"}
JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


class StoryProblem(Exception):
    """The story can't be imported as asked. The message says why."""


def parse_ref(ref: str) -> tuple[str, str]:
    """'KYC-12', a Jira /browse/ address, '#45', '45' or a GitHub issue address → (tracker, key)."""
    ref = ref.strip()
    if m := re.search(r"/browse/([A-Z][A-Z0-9_]+-\d+)", ref):
        return "jira", m.group(1)
    if JIRA_KEY.match(ref.upper()) and not ref.isdigit():
        return "jira", ref.upper()
    if m := re.match(r"https?://github\.com/[\w.-]+/[\w.-]+/issues/(\d+)", ref):
        return "github", f"#{m.group(1)}"
    if m := re.fullmatch(r"#?(\d+)", ref):
        return "github", f"#{m.group(1)}"
    raise StoryProblem("Write the story as a Jira key (KYC-12), a GitHub issue number (#45), or its address.")


async def fetch(project: models.Project, ref: str) -> dict:
    """The story as the tracker has it: key, title, description, address, status and (Jira) its sprint."""
    tracker_name, key = parse_ref(ref)
    if tracker_name == "jira" and project.jira_project_key and not key.startswith(project.jira_project_key + "-"):
        raise StoryProblem(f"{key} is not in this project's Jira project ({project.jira_project_key}).")
    try:
        issue = await get_tracker(tracker_name, project).issue(key)
    except TrackerError as err:
        raise StoryProblem(str(err))
    return {"key": issue.key, "title": issue.title[:300], "description": (issue.body or issue.snippet or "").strip() or None,
            "url": issue.url, "tracker": tracker_name, "tracker_status": issue.status, "closed": issue.closed,
            "sprint": issue.sprint}


def _sessions(story: models.Story) -> list[models.Session]:
    return sorted((s for c in story.charters for s in c.sessions), key=lambda s: s.started_at)


def _bugs(sessions: list[models.Session]) -> list[models.Bug]:
    return sorted((b for s in sessions for b in s.bugs), key=lambda b: b.created_at)


def _left(story: models.Story, sessions: list[models.Session], bugs: list[models.Bug]) -> list[dict]:
    """What hasn't been looked at yet, each with where to act. Not a verdict."""
    left: list[dict] = []
    risks = [r for r in story.risks if r.status != "retired"]
    if not risks:
        left.append({"text": "No risks linked yet: what could this change break, and for whom?", "to": "#risks", "action": "Link a risk"})
    if not story.charters:
        left.append({"text": "No charters yet: nothing is planned to explore this story.", "to": "#charters", "action": "Add a charter"})
    covered = {r.id for c in story.charters for r in c.risks}
    for r in sorted(risks, key=lambda r: -r.score):
        if r.id not in covered:
            left.append({"text": f"{r.ref} {r.title}: no charter in this story investigates it.", "to": "#charters", "action": "Add a charter"})
    for c in story.charters:
        if not c.sessions:
            left.append({"text": f"Not explored yet: {c.mission_statement}", "to": "/charters/suggest", "action": "Start a session"})
    latest = {s.charter_id: s for s in sessions if s.debrief}  # sessions are oldest first; a later one may fulfil the charter
    for s in sessions:
        d = s.debrief
        if not s.ended_at and s.paused_at and s.tester_id == "Mirmera agent":
            left.append({"text": f"Mirmera explored it: {s.charter.mission_statement}. Review its notes and end the session for the debrief.",
                         "to": f"/sessions/{s.id}", "action": "Review session"})
        elif s.ended_at and not d:
            left.append({"text": f"No debrief yet: {s.charter.mission_statement} ({s.tester_id})",
                         "to": f"/sessions/{s.id}/debrief", "action": "Write debrief"})
        elif d and d.charter_status in UNFULFILLED and latest[s.charter_id] is s:
            why = f": {d.charter_status_rationale}" if d.charter_status_rationale else ""
            left.append({"text": f"Charter {UNFULFILLED[d.charter_status]}: {s.charter.mission_statement}{why}",
                         "to": f"/sessions/{s.id}/debrief", "action": "Open debrief"})
    unreviewed = [b for b in bugs if b.kind is None]
    if unreviewed:
        left.append({"text": f"{len(unreviewed)} bug report{'s' if len(unreviewed) != 1 else ''} to review",
                     "to": "/bugs", "action": "Review"})
    for b in bugs:
        if b.kind == "bug" and b.severity == "high" and not b.filings:
            left.append({"text": f"High-severity bug not filed: {b.title}", "to": "/bugs", "action": "File it"})
    return left


def summary(story: models.Story, now: datetime | None = None) -> dict:
    now = now or utcnow()
    sessions = _sessions(story)
    bugs = _bugs(sessions)
    return {
        "id": story.id, "key": story.key, "title": story.title, "url": story.url, "tracker": story.tracker,
        "tracker_status": story.tracker_status, "closed": bool(story.closed), "created_at": story.created_at,
        "risks": len([r for r in story.risks if r.status != "retired"]), "charters": len(story.charters),
        "sessions": len(sessions), "minutes": round(sum(tested_seconds(s, now) for s in sessions) / 60),
        "bugs": len([b for b in bugs if b.kind != "question"]),
        "high_bugs": len([b for b in bugs if b.kind == "bug" and b.severity == "high"]),
        "left": len(_left(story, sessions, bugs)),
        "sprint": {"id": story.sprint.id, "name": story.sprint.name} if story.sprint else None,
    }


def detail(db: DBSession, story: models.Story) -> dict:
    now = utcnow()
    sessions = _sessions(story)
    bugs = _bugs(sessions)
    story_charter_ids = {c.id for c in story.charters}
    risks = [{
        "id": r.id, "ref": r.ref, "title": r.title, "failure_mode": r.failure_mode, "score": r.score,
        "status": r.status, "quality_criterion": r.quality_criterion,
        "charters": len([c for c in r.charters if c.id in story_charter_ids]),
        "other_stories": [st.ref for st in r.stories if st.id != story.id],
    } for r in sorted(story.risks, key=lambda r: (r.status == "retired", -r.score))]
    charters = [{
        "id": c.id, "mission_statement": c.mission_statement, "status": c.status, "priority": c.priority,
        "source": c.source, "rationale": c.rationale,  # a follow-up says why it was suggested
        "risk_refs": [r.ref for r in c.risks], "sessions": len(c.sessions),
        "minutes": round(sum(tested_seconds(s, now) for s in c.sessions) / 60),
        "bugs": len([b for s in c.sessions for b in s.bugs if b.kind != "question"]),
    } for c in sorted(story.charters, key=lambda c: c.created_at)]
    session_rows = [{
        "id": s.id, "charter_id": s.charter_id, "mission_statement": s.charter.mission_statement, "tester": s.tester_id,
        "started_at": s.started_at, "ended_at": s.ended_at, "minutes": round(tested_seconds(s, now) / 60),
        "bugs": len([b for b in s.bugs if b.kind != "question"]), "debrief": s.debrief is not None,
        "paused": bool(s.paused_at and not s.ended_at),
    } for s in sessions]
    bug_rows = [{
        "id": b.id, "title": b.title, "kind": b.kind, "severity": b.severity, "session_id": b.session_id,
        "issue_key": b.filings[-1].issue_key if b.filings else None,
        "issue_url": b.filings[-1].issue_url if b.filings else None,
    } for b in bugs]

    pages: Counter = Counter()
    actions: Counter = Counter()
    seen: dict[str, set] = {}
    if sessions:
        evidence = (db.query(models.Evidence)
                    .filter(models.Evidence.session_id.in_([s.id for s in sessions]),
                            models.Evidence.kind.in_(("navigation", "action"))).all())
        for e in evidence:
            key = page_key(e.url)
            if not key:
                continue
            seen.setdefault(key, set()).add(e.session_id)
            if e.kind == "action":
                actions[key] += 1
        for key, ids in seen.items():
            pages[key] = len(ids)
    page_rows = [{"page": k, "sessions": n, "actions": actions[k]}
                 for k, n in sorted(pages.items(), key=lambda kv: (-kv[1], -actions[kv[0]], kv[0]))[:PAGES_SHOWN]]

    return {
        **summary(story, now),
        "description": story.description, "artifact_id": story.artifact_id,
        "risk_list": risks, "charter_list": charters, "session_list": session_rows, "bug_list": bug_rows,
        "pages": page_rows, "more_pages": max(len(pages) - PAGES_SHOWN, 0),
        "left_list": _left(story, sessions, bugs),
    }


def artifact_text(story: models.Story) -> str:
    """What the AI reads to suggest charters for the story."""
    head = f"User story {story.key}: {story.title}" if story.key else f"User story: {story.title}"
    return f"{head}\n\n{story.description or '(no description)'}".strip()


def unlink_all(db: DBSession, *, story_ids=(), risk_ids=(), charter_ids=()) -> None:
    """Remove link rows before stories, risks or charters are deleted."""
    if story_ids:
        db.execute(models.story_risks.delete().where(models.story_risks.c.story_id.in_(story_ids)))
        db.execute(models.story_charters.delete().where(models.story_charters.c.story_id.in_(story_ids)))
    if risk_ids:
        db.execute(models.story_risks.delete().where(models.story_risks.c.risk_id.in_(risk_ids)))
    if charter_ids:
        db.execute(models.story_charters.delete().where(models.story_charters.c.charter_id.in_(charter_ids)))
