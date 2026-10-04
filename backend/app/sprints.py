"""Sprints: a name, dates and the stories planned for them.

A sprint's testing is everything done in its dates, not only what was done under its
stories: testing that went outside them (a risk no story names, a follow-up, a bug
hunt) is shown too, because "what did we test this sprint?" deserves the whole answer.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session as DBSession

from app import models, stories
from app.project_report import HIGH_RISK, tested_seconds
from app.timeutil import utcnow


def bounds(sprint: models.Sprint) -> tuple[datetime, datetime]:
    """Whole days, UTC: the end day is included."""
    return datetime.combine(sprint.starts_on, time.min), datetime.combine(sprint.ends_on + timedelta(days=1), time.min)


def state(sprint: models.Sprint, today: date | None = None) -> str:
    today = today or utcnow().date()
    return "upcoming" if today < sprint.starts_on else "past" if today > sprint.ends_on else "current"


def _period_sessions(db: DBSession, sprint: models.Sprint) -> list[models.Session]:
    start, end = bounds(sprint)
    return (db.query(models.Session).join(models.Charter)
            .filter(models.Charter.project_id == sprint.project_id,
                    models.Session.started_at >= start, models.Session.started_at < end)
            .order_by(models.Session.started_at).all())


def summary(db: DBSession, sprint: models.Sprint) -> dict:
    now = utcnow()
    story_rows = [stories.summary(st, now) for st in sprint.stories]
    sessions = _period_sessions(db, sprint)
    return {
        "id": sprint.id, "name": sprint.name, "goal": sprint.goal, "starts_on": sprint.starts_on, "ends_on": sprint.ends_on,
        "state": state(sprint), "days_left": max((sprint.ends_on - now.date()).days + 1, 0) if state(sprint) == "current" else None,
        "stories": len(story_rows), "stories_done": len([s for s in story_rows if s["closed"]]),
        "sessions": len(sessions), "minutes": round(sum(tested_seconds(s, now) for s in sessions) / 60),
        "bugs": len([b for s in sessions for b in s.bugs if b.kind != "question"]),
        "left": sum(s["left"] for s in story_rows),
    }


def detail(db: DBSession, sprint: models.Sprint) -> dict:
    now = utcnow()
    sprint_stories = sorted(sprint.stories, key=lambda st: (bool(st.closed), st.created_at))
    story_charters = {c.id for st in sprint_stories for c in st.charters}
    sessions = _period_sessions(db, sprint)
    inside = [s for s in sessions if s.charter_id in story_charters]
    outside = [s for s in sessions if s.charter_id not in story_charters]

    def part(rows: list[models.Session]) -> dict:
        return {"sessions": len(rows), "minutes": round(sum(tested_seconds(s, now) for s in rows) / 60),
                "bugs": len([b for s in rows for b in s.bugs if b.kind != "question"])}

    outside_by_charter: dict[str, list[models.Session]] = defaultdict(list)
    for s in outside:
        outside_by_charter[s.charter_id].append(s)
    outside_charters = [{
        "id": rows[0].charter.id, "mission_statement": rows[0].charter.mission_statement,
        "sessions": len(rows), "minutes": round(sum(tested_seconds(s, now) for s in rows) / 60),
        "stories": [st.ref for st in rows[0].charter.stories],  # under another sprint's story, or none
    } for rows in outside_by_charter.values()]

    explored = {r.id for s in sessions for r in s.charter.risks}
    in_sprint_risks = {r.id for st in sprint_stories for r in st.risks}
    high = (db.query(models.Risk).filter(models.Risk.project_id == sprint.project_id, models.Risk.status == "accepted").all())
    high_unexplored = [{"id": r.id, "ref": r.ref, "title": r.title, "score": r.score, "in_sprint": r.id in in_sprint_risks}
                       for r in sorted(high, key=lambda r: -r.score) if r.score >= HIGH_RISK and r.id not in explored]

    left: list[dict] = []
    sprint_state = state(sprint)
    for st in sprint_stories:
        label = st.key or st.title[:40]
        if sprint_state == "past" and not st.closed:
            left.append({"text": f"{label}: not done, and the sprint has ended", "to": f"/stories/{st.id}", "action": "Open story"})
        for item in stories.detail(db, st)["left_list"]:
            to = item["to"] if not item["to"].startswith("#") else f"/stories/{st.id}{item['to']}"
            left.append({**item, "text": f"{label}: {item['text']}", "to": to})
    for r in high_unexplored:
        where = "linked to a story here" if r["in_sprint"] else "not in any story of this sprint"
        left.append({"text": f"High risk not explored in this sprint: {r['ref']} {r['title']} (score {r['score']}, {where})",
                     "to": "/risks", "action": "Make a charter"})

    return {
        **summary(db, sprint),
        "story_list": [{**stories.summary(st, now)} for st in sprint_stories],
        "under_stories": part(inside), "outside_stories": part(outside), "outside_charters": outside_charters,
        "high_unexplored": high_unexplored, "left_list": left,
    }


def current(db: DBSession, project_id: str) -> models.Sprint | None:
    today = utcnow().date()
    return (db.query(models.Sprint).filter(models.Sprint.project_id == project_id, models.Sprint.starts_on <= today,
                                           models.Sprint.ends_on >= today)
            .order_by(models.Sprint.starts_on.desc()).first())
