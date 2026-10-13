"""Learning from dismissed drafts: a tester says why a draft isn't a bug, and Mirmera doesn't
draft that problem again in this project.

A dismissal keeps the problem's fingerprint (the same key the tracker check uses: a failing
request by its path, a wrong answer by its rule and words, else the title), the reason and the
tester's note. Before drafting, Mirmera looks for it; the AI also reads the project's dismissals,
so it stops raising the same thing in other words. "Can't reproduce" is the exception: the
problem is still drafted when it shows again, marked as dismissed before (it may be intermittent).
Undoing a dismissal makes the draft a draft again and forgets the dismissal.
"""
from sqlalchemy.orm import Session as DBSession

from app import models

REASONS = {
    "by_design": "works as intended",
    "not_a_problem": "not a problem",
    "not_important": "not worth fixing",
    "duplicate": "already reported",
    "cant_reproduce": "can't reproduce",
}
STILL_DRAFTED = {"cant_reproduce"}  # shown again: drafted, with the earlier dismissal mentioned


def for_problem(db: DBSession, project_id: str, fingerprint: str) -> models.Dismissal | None:
    return (db.query(models.Dismissal).filter(models.Dismissal.project_id == project_id,
                                              models.Dismissal.fingerprint == fingerprint)
            .order_by(models.Dismissal.created_at.desc()).first())


def describe(d: models.Dismissal) -> str:
    when = d.created_at.strftime("%d %b") if d.created_at else "earlier"
    return (f'dismissed on {when}{" by " + d.by if d.by else ""} as {REASONS.get(d.reason, d.reason)}'
            + (f': "{d.note[:200]}"' if d.note else ""))


def for_ai(db: DBSession, project_id: str, limit: int = 12) -> list[str]:
    """What the team dismissed in this project, for the AI: not to report again in other words."""
    rows = (db.query(models.Dismissal).filter(models.Dismissal.project_id == project_id,
                                              models.Dismissal.reason.notin_(STILL_DRAFTED))
            .order_by(models.Dismissal.created_at.desc()).limit(limit).all())
    return [f'"{d.title[:120]}": {REASONS.get(d.reason, d.reason)}' + (f" ({d.note[:160]})" if d.note else "") for d in rows]
