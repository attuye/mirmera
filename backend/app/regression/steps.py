"""The steps that lead to a bug, for its regression test and the reproduction agent.

Each bug owns what was recorded between the previous bug of its session and
itself: two bugs found in one session must not share each other's actions.

From that recording Mirmera proposes the steps itself when it can see where
the bug happened: a request that failed. The steps then run from where this
attempt started up to the action that sent that request; the AI only writes the
checks. Without such an anchor, the AI picks steps from the bug's recording.
Either way the tester can edit the steps (remove, reorder, change, add), and
edited steps are used exactly as saved.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy.orm import Session as DBSession

from app import models
from app.runner import describe_action, sanitize_action

BUG_GRACE_SECONDS = 5  # actions recorded just after logging the bug still count
MAX_STEPS = 40  # proposed steps; longer recordings keep the last ones
MAX_EDITED_STEPS = 60


@dataclass
class BugRecording:
    """What was recorded for one bug, oldest first."""
    actions: list[models.Evidence] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)  # visited URLs, unique, in order
    failed: list[models.Evidence] = field(default_factory=list)


@dataclass
class BugSteps:
    steps: list[dict]  # resolved plan steps: {"type": "goto", "url"} | {"type": "action", "action", "url"?}
    source: str  # "edited" | "recording" (picked by Mirmera) | "ai" (the AI will pick from `steps`)
    note: str
    anchor: str | None = None  # the failed request the steps lead to
    reach: str | None = None  # URL part the test must request to reach the bug, e.g. "/api/verify"

    @property
    def fixed(self) -> bool:
        return self.source in ("edited", "recording")


def window_start(db: DBSession, bug: models.Bug):
    """When the previous bug of the same session was logged (None for the first bug)."""
    prev = (db.query(models.Bug.created_at)
            .filter(models.Bug.session_id == bug.session_id, models.Bug.id != bug.id,
                    models.Bug.created_at < bug.created_at)
            .order_by(models.Bug.created_at.desc()).first())
    return prev[0] if prev else None


def recording_for(db: DBSession, bug: models.Bug) -> BugRecording:
    start, end = window_start(db, bug), bug.created_at + timedelta(seconds=BUG_GRACE_SECONDS)

    def kind(k: str) -> list[models.Evidence]:
        q = db.query(models.Evidence).filter(models.Evidence.session_id == bug.session_id,
                                             models.Evidence.kind == k, models.Evidence.captured_at <= end)
        if start is not None:
            q = q.filter(models.Evidence.captured_at > start)
        return q.order_by(models.Evidence.captured_at).all()

    navigations = kind("navigation")
    if not navigations:  # stayed on one page since the previous bug: that page is where it happened
        last = (db.query(models.Evidence)
                .filter(models.Evidence.session_id == bug.session_id, models.Evidence.kind == "navigation",
                        models.Evidence.captured_at <= end)
                .order_by(models.Evidence.captured_at.desc()).first())
        navigations = [last] if last else []
    failed = [e for e in kind("failed_request") if not e.summary.startswith("Could not open ")]
    return BugRecording(actions=kind("action"), pages=list(dict.fromkeys(e.url for e in navigations if e.url)),
                        failed=failed)


def _path(url: str | None) -> str:
    return urlsplit(url or "").path or "/"


def _action_step(e: models.Evidence) -> dict:
    return {"type": "action", "action": {**(e.data or {}), "summary": e.summary}, "url": e.url}


def anchor_of(rec: BugRecording) -> models.Evidence | None:
    """The failed request the bug shows as: the first one after the tester started acting
    (a 404 while a page loads, before any action, is background noise)."""
    if not rec.actions:
        return None
    first_action = rec.actions[0].captured_at
    return next((f for f in rec.failed if f.captured_at >= first_action), None)


def proposed_steps(rec: BugRecording) -> BugSteps:
    anchor = anchor_of(rec)
    if anchor is None:
        steps = ([{"type": "goto", "url": rec.pages[0]}] if rec.pages else []) + [_action_step(e) for e in rec.actions]
        why = ("No request failed before this bug, so Mirmera can't tell which actions lead to it: the AI picks "
               "from these when it writes the test. Edit them to choose the steps yourself.")
        if not rec.actions:
            why = ("Only the page was recorded for this bug, no actions: add the steps that lead to it, or let the AI "
                   "work from the page." if rec.pages else "Nothing was recorded for this bug: add the steps that lead to it.")
        return BugSteps(steps=steps, source="ai", note=why)

    actions = [e for e in rec.actions if e.captured_at <= anchor.captured_at]
    cut = len(actions) > MAX_STEPS
    actions = actions[-MAX_STEPS:]
    start = actions[0].url or (rec.pages[0] if rec.pages else None)  # where the attempt began
    steps = ([{"type": "goto", "url": start}] if start else []) + [_action_step(e) for e in actions]
    note = (f"Picked by Mirmera from the recording: from {_path(start)} up to the action that sent the request that "
            f"failed ({anchor.summary}). The AI only writes the checks.")
    if cut:
        note += f" Only the last {MAX_STEPS} actions were kept: add any setup steps that came before."
    return BugSteps(steps=steps, source="recording", note=note, anchor=anchor.summary, reach=_path(anchor.url))


def steps_for(db: DBSession, bug: models.Bug) -> tuple[BugSteps, BugRecording]:
    rec = recording_for(db, bug)
    proposed = proposed_steps(rec)
    if bug.edited_steps:
        return BugSteps(steps=bug.edited_steps, source="edited",
                        note="Edited by you. The regression test and the reproduction agent use these steps as they are.",
                        anchor=proposed.anchor, reach=proposed.reach), rec
    return proposed, rec


# ---------- tester edits ----------

class StepError(ValueError):
    pass


def clean_steps(raw: list) -> list[dict]:
    """Validates steps sent by the tester: the same shapes the recorder produces."""
    if not isinstance(raw, list) or not raw:
        raise StepError("Give at least one step.")
    if len(raw) > MAX_EDITED_STEPS:
        raise StepError(f"At most {MAX_EDITED_STEPS} steps.")
    out = []
    for i, s in enumerate(raw, 1):
        if not isinstance(s, dict):
            raise StepError(f"Step {i} is not a step.")
        if s.get("type") == "goto":
            url = str(s.get("url") or "").strip()
            if not url.startswith(("http://", "https://")) or len(url) > 2000:
                raise StepError(f"Step {i}: the page to open must be a full http(s) address.")
            out.append({"type": "goto", "url": url})
        elif s.get("type") == "action":
            action = sanitize_action(s.get("action"))
            if action is None:
                raise StepError(f"Step {i}: not a valid action (it needs an action and a target).")
            loc = action["locator"]
            if loc["method"] == "role" and not (loc.get("role") and loc.get("name")):
                raise StepError(f"Step {i}: a role target needs a role and a name, e.g. button \"Verify\".")
            if loc["method"] != "role" and not loc.get("value"):
                raise StepError(f"Step {i}: the target needs a value, e.g. the field's label.")
            if action["action"] in ("fill", "select") and not action.get("masked") and "value" not in action:
                raise StepError(f"Step {i}: {action['action']} needs a value.")
            if action["action"] == "press" and not action.get("key"):
                raise StepError(f"Step {i}: press needs a key, e.g. Enter.")
            action["summary"] = describe_action(action)
            out.append({"type": "action", "action": action, **({"url": s["url"]} if isinstance(s.get("url"), str) else {})})
        else:
            raise StepError(f"Step {i}: type must be goto or action.")
    if out[0]["type"] != "goto":
        raise StepError("The first step must open a page.")
    return out


def describe_step(step: dict) -> str:
    return f"open {step['url']}" if step["type"] == "goto" else (step["action"].get("summary") or describe_action(step["action"]))


def bug_symptom(bug: models.Bug, st: BugSteps) -> dict | None:
    """What shows the bug: the failed request the steps lead to, else the text the
    report says actually shows. {"kind": "failed_request" | "text", "value"}."""
    if st.reach:
        return {"kind": "failed_request", "value": st.reach}
    if (bug.actual or "").strip():
        return {"kind": "text", "value": bug.actual.strip()[:300]}
    return None
