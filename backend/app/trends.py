"""Testing trends: how a project's testing moves week by week.

Everything is counted from what was recorded, in Monday-to-Sunday weeks (UTC):
sessions, hours tested (pauses left out), testers, bugs found by severity,
bugs per hour of testing, where the time went, charter outcomes, fixes checked
after an issue closed, and the risk exposure (the sum of the accepted risks'
scores at the end of each week, rebuilt from their re-score history).
"""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session as DBSession

from app import models
from app.project_report import PHASES, _time_split, tested_seconds
from app.timeutil import utcnow

COMPARE_WEEKS = 4  # "what changed": the last four weeks against the four before


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _score_at(risk: models.Risk, changes: list[models.RiskChange], at: datetime) -> int:
    """The risk's score at a moment: the last accepted re-score before it, else where it started."""
    done = [c for c in changes if c.status == "accepted" and c.decided_at and c.decided_at <= at]
    if done:
        last = done[-1]
        return (last.final_impact or last.to_impact) * (last.final_likelihood or last.to_likelihood)
    first = next((c for c in changes if c.status == "accepted"), None)
    return first.from_impact * first.from_likelihood if first else risk.score


def build(db: DBSession, project: models.Project, weeks: int = 12) -> dict:
    now = utcnow()
    first = week_start(now.date()) - timedelta(weeks=weeks - 1)
    starts = [first + timedelta(weeks=i) for i in range(weeks)]
    begin = datetime.combine(first, time.min)
    index = lambda ts: (ts.date() - first).days // 7 if ts and ts >= begin else None  # noqa: E731

    charters = db.query(models.Charter).filter(models.Charter.project_id == project.id).all()
    sessions_by_week: dict[int, list[models.Session]] = defaultdict(list)
    for c in charters:
        for s in c.sessions:
            i = index(s.started_at)
            if i is not None and i < weeks:
                sessions_by_week[i].append(s)
    bugs = (db.query(models.Bug).filter(models.Bug.charter_id.in_([c.id for c in charters])).all() if charters else [])
    retests = db.query(models.Retest).filter(models.Retest.project_id == project.id).all()
    risks = [r for r in project.risks if r.status == "accepted"]

    rows = []
    for i, start in enumerate(starts):
        sessions = sessions_by_week.get(i, [])
        seconds = sum(tested_seconds(s, now) for s in sessions)
        week_bugs = [b for b in bugs if index(b.created_at) == i]
        sev = Counter(b.severity for b in week_bugs if b.kind == "bug")
        found = sum(sev.values())
        outcomes = Counter(s.debrief.charter_status for s in sessions if s.debrief and s.debrief.charter_status)
        week_retests = Counter(r.outcome for r in retests if index(r.created_at) == i)
        end = datetime.combine(start + timedelta(days=7), time.min)
        exposure = sum(_score_at(r, sorted(r.changes, key=lambda c: c.decided_at or c.created_at), end)
                       for r in risks if r.created_at < end)
        rows.append({
            "week": start.isoformat(), "sessions": len(sessions), "hours": round(seconds / 3600, 2),
            "testers": len({s.tester_id for s in sessions}),
            "bugs": {"high": sev.get("high", 0), "medium": sev.get("medium", 0), "low": sev.get("low", 0)},
            "questions": sum(1 for b in week_bugs if b.kind == "question"),
            "unreviewed": sum(1 for b in week_bugs if not b.kind),
            "bugs_per_hour": round(found / (seconds / 3600), 2) if seconds >= 600 else None,  # under 10 min: too little to say
            "time_split": {k: round(v, 3) for k, v in _time_split(sessions, now).items()},
            "charters": {"fulfilled": outcomes.get("fulfilled", 0), "partly": outcomes.get("partially_fulfilled", 0),
                         "not": outcomes.get("not_fulfilled", 0)},
            "fixes": {"verified": week_retests.get("fixed", 0), "still_fails": week_retests.get("still_fails", 0)},
            "risk_exposure": exposure if risks else None,
        })
    return {"project_id": project.id, "weeks": weeks, "generated_at": now, "phases": PHASES,
            "rows": rows, "changes": _changes(rows)}


def _changes(rows: list[dict]) -> list[dict]:
    """The last four weeks against the four before, in words; only what moved."""
    if len(rows) < 2 * COMPARE_WEEKS:
        return []
    recent, before = rows[-COMPARE_WEEKS:], rows[-2 * COMPARE_WEEKS:-COMPARE_WEEKS]
    total = lambda part, f: sum(f(r) for r in part)  # noqa: E731
    out = []

    h1, h0 = total(recent, lambda r: r["hours"]), total(before, lambda r: r["hours"])
    if h1 or h0:
        out.append(_moved("Testing time", h0, h1, lambda v: f"{v:.1f} h"))
    b1 = total(recent, lambda r: sum(r["bugs"].values()))
    b0 = total(before, lambda r: sum(r["bugs"].values()))
    if b1 or b0:
        out.append(_moved("Bugs found", b0, b1, lambda v: f"{v:g}"))
    if h1 >= 1 and h0 >= 1:
        r1, r0 = b1 / h1, b0 / h0
        line = _moved("Bugs per hour of testing", r0, r1, lambda v: f"{v:.2f}")
        if line["direction"] == "down":
            line["note"] = "Fewer new bugs for the time spent: a stabler area, or testing that keeps to familiar paths."
        elif line["direction"] == "up":
            line["note"] = "More bugs for the time spent: a weaker area, or sharper testing."
        out.append(line)
    hi1, hi0 = total(recent, lambda r: r["bugs"]["high"]), total(before, lambda r: r["bugs"]["high"])
    if hi1 or hi0:
        out.append(_moved("High-severity bugs", hi0, hi1, lambda v: f"{v:g}"))
    e1, e0 = recent[-1]["risk_exposure"], before[-1]["risk_exposure"]
    if e1 is not None and e0 is not None and (e1 or e0):
        out.append(_moved("Risk exposure", e0, e1, lambda v: f"{v:g}"))
    return [c for c in out if c]


def _moved(name: str, before: float, recent: float, fmt) -> dict:
    if before == recent:
        direction, text = "same", f"{name}: no change ({fmt(recent)})"
    else:
        direction = "up" if recent > before else "down"
        pct = f" ({'+' if recent > before else '−'}{abs(recent - before) / before:.0%})" if before else ""
        text = f"{name}: {fmt(before)} → {fmt(recent)}{pct}"
    return {"name": name, "before": before, "recent": recent, "direction": direction, "text": text, "note": None}
