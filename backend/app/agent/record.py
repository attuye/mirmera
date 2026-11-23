"""The agent's track record: what testers made of its drafts, so they know how far to trust it.

From the Bachs' Mecha-Testing, where Claude keeps "demerits" for its own mistakes. Mirmera doesn't need
the AI to confess: every draft is reviewed by a tester, who saves it as a bug or a question, or dismisses
it with a reason. Counting those, split by who decided the draft was a problem (Mirmera's own checks in
plain code, or the AI judging a story rule broken), says where the agent is right and where it isn't.
"""
from collections import Counter

from sqlalchemy.orm import Session as DBSession

from app import models
from app.dismissals import REASONS

AGENT = "Mirmera agent"
JUDGES = {"mirmera": "Mirmera's own checks", "ai": "the AI's judgement", None: "before this was recorded"}


def outcome(bug: models.Bug) -> str:
    if bug.dismissal:
        return "dismissed"
    if bug.kind == "bug":
        return "bug"
    if bug.kind == "question":
        return "question"
    return "waiting"


def _tally(bugs: list[models.Bug]) -> dict:
    c = Counter(outcome(b) for b in bugs)
    decided = c["bug"] + c["question"] + c["dismissed"]
    replays = [b.confirmation for b in bugs if b.confirmation and b.confirmation.get("runs")]
    return {"drafts": len(bugs), "bugs": c["bug"], "questions": c["question"], "dismissed": c["dismissed"],
            "waiting": c["waiting"], "kept_share": round(c["bug"] / decided, 2) if decided else None,
            "dismissed_for": dict(Counter(REASONS.get((b.dismissal or {}).get("reason"), "other")
                                          for b in bugs if b.dismissal)),
            "replayed": len(replays), "every_time": sum(1 for r in replays if r["reproduced"] == r["runs"]),
            "never_again": sum(1 for r in replays if r["reproduced"] == 0)}


def record(db: DBSession, project_id: str) -> dict:
    """The project's record: overall, by who judged, and per run (latest first)."""
    bugs = (db.query(models.Bug).join(models.Charter, models.Bug.charter_id == models.Charter.id)
            .filter(models.Charter.project_id == project_id).all())
    mine = [b for b in bugs if (b.environment or {}).get("found_by") == AGENT]
    by: dict = {}
    for b in mine:
        by.setdefault((b.environment or {}).get("judged_by"), []).append(b)
    runs = (db.query(models.ExplorationRun).filter(models.ExplorationRun.project_id == project_id)
            .order_by(models.ExplorationRun.created_at.desc()).limit(10).all())
    ids = {b.id: b for b in mine}
    recent = []
    for r in runs:
        drafts = [ids[i] for i in (r.bug_ids or []) if i in ids]
        if drafts:
            charter = db.get(models.Charter, r.charter_id)
            recent.append({"run_id": r.id, "session_id": r.session_id, "at": r.created_at.isoformat() if r.created_at else None,
                           "mission": charter.mission_statement if charter else "", **_tally(drafts)})
    return {"overall": _tally(mine),
            "by_judge": [{"judge": k or "unknown", "label": JUDGES.get(k, k), **_tally(v)}
                         for k, v in sorted(by.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))],
            "runs": recent}


def line(r: dict) -> str:
    """One sentence for a run's summary: what testers made of the agent's drafts so far."""
    o = r["overall"]
    if not o["drafts"]:
        return ""
    parts = [f"{o['bugs']} saved as bugs", f"{o['questions']} as questions", f"{o['dismissed']} dismissed"]
    s = f"The agent's drafts in this project so far: {', '.join(parts)}, {o['waiting']} waiting for review."
    ai = next((j for j in r["by_judge"] if j["judge"] == "ai"), None)
    if ai and (ai["bugs"] + ai["questions"] + ai["dismissed"]):
        s += f" Where the AI judged a rule broken: {ai['bugs']} of {ai['bugs'] + ai['questions'] + ai['dismissed']} kept as bugs."
    return s
