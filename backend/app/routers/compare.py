"""Compare two versions or environments: a session's journey, side by side (see app/compare.py)."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession

from app import compare as cmp
from app import models, schemas
from app.auth import current_user
from app.config import settings
from app.database import get_db
from app.regression.replay import _rebase
from app.regression.steps import clean_steps, describe_step, StepError
from app.routers import runner as runner_api
from app.runner import RunnerUnavailable, manager

router = APIRouter(tags=["compare"])


def _session(db: DBSession, session_id: str) -> models.Session:
    s = db.get(models.Session, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


def _comparison(db: DBSession, comparison_id: str) -> models.Comparison:
    c = db.get(models.Comparison, comparison_id)
    if not c:
        raise HTTPException(404, "Comparison not found")
    return c


@router.post("/sessions/{session_id}/comparisons", response_model=schemas.ComparisonOut)
def compare(session_id: str, payload: schemas.CompareRequest, request: Request, db: DBSession = Depends(get_db)):
    """Does the session's journey on both addresses, a step at a time, and keeps what differed."""
    session = _session(db, session_id)
    journey = cmp.journey_of(db, session)
    if len(journey) < 2:
        raise HTTPException(409, "Nothing to compare yet: this session has no recorded steps. Explore in the "
                                 "session's browser (or let Mirmera explore), then compare.")
    project = session.charter.project
    from app import sign_in
    envs = [payload.a.model_dump(), payload.b.model_dump()]
    row = models.Comparison(session_id=session.id, env_a=envs[0], env_b=envs[1], journey=journey,
                            started_by=current_user(request, db).name)
    db.add(row)
    db.flush()
    try:
        result = manager.loop.run(
            cmp.compare(journey, envs, recorded_base=cmp.recorded_base_of(journey), key=row.id,
                        folder=runner_api.session_folder(session.id), executable_path=settings.runner_chromium_path,
                        sign_in_cfg=sign_in.for_run(project)), timeout=600)
        row.steps, row.summary = result["steps"], result["summary"]
    except ImportError as err:
        raise RunnerUnavailable("Playwright is not installed. Run: python -m playwright install chromium") from err
    except Exception as err:
        row.status, row.detail = "error", (str(err).strip().splitlines() or [type(err).__name__])[0][:500]
        for secret in sign_in.secrets(sign_in.for_run(project)):
            row.detail = row.detail.replace(secret, "•••")
    db.add(models.Note(session_id=session.id, tag="test",
                       content=(f"Compared {envs[0]['name']} ({envs[0]['url']}) with {envs[1]['name']} ({envs[1]['url']}): "
                                + (row.summary or f"couldn't finish: {row.detail}"))[:4000]))
    db.commit()
    db.refresh(row)
    return row


@router.get("/sessions/{session_id}/comparisons", response_model=list[schemas.ComparisonOut])
def comparisons(session_id: str, db: DBSession = Depends(get_db)):
    _session(db, session_id)
    return (db.query(models.Comparison).filter(models.Comparison.session_id == session_id)
            .order_by(models.Comparison.created_at.desc()).limit(10).all())


@router.get("/comparisons/{comparison_id}/shots/{name}")
def shot(comparison_id: str, name: str, db: DBSession = Depends(get_db)):
    c = _comparison(db, comparison_id)
    if name not in {s for step in c.steps or [] for s in step.get("shots", [])}:
        raise HTTPException(404, "No such screenshot")
    path = runner_api.session_folder(c.session_id) / Path(name).name
    if not path.is_file():
        raise HTTPException(404, "The screenshot is gone (evidence was cleaned up)")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/comparisons/{comparison_id}/steps/{n}/bug", response_model=schemas.ComparisonOut)
def draft_bug(comparison_id: str, n: int, payload: schemas.CompareBugRequest, db: DBSession = Depends(get_db)):
    """A bug draft from one step that differed: the other environment, doing the same steps, is the oracle."""
    c = _comparison(db, comparison_id)
    step = next((s for s in c.steps or [] if s["n"] == n), None)
    if not step or not step["differences"]:
        raise HTTPException(404, "That step didn't differ")
    right, wrong = (c.env_a, c.env_b) if payload.right == "a" else (c.env_b, c.env_a)
    rs, ws = (step["a"], step["b"]) if payload.right == "a" else (step["b"], step["a"])
    base = cmp.recorded_base_of(c.journey)
    steps = [{**s, "url": _rebase(s["url"], base, wrong["url"])} if s.get("url") and s["type"] == "goto" else s
             for s in c.journey[:n]]
    try:
        edited = clean_steps(steps)
    except StepError:
        edited = None
    new_text = [m for m in ws.get("messages", []) if m not in rs.get("messages", [])]
    failed = [cmp._request(f) for f in ws.get("failed", []) if f not in rs.get("failed", [])]
    symptom = ({"kind": "text_visible", "value": new_text[0][:300], "explanation": f"not shown on {right['name']}"} if new_text
               else {"kind": "failed_request", "value": failed[0].split(" ")[1], "explanation": failed[0]} if failed else None)

    def seen(s: dict) -> str:
        bits = [f"on {s.get('path') or '?'}"] + [f'it says "{m[:120]}"' for m in s.get("messages", [])[:2]] \
            + [f"{cmp._request(f)}" for f in s.get("failed", [])[:2]] + ([f"step {s['status']}: {s['detail']}"] if s["status"] != "ok" else [])
        return ", ".join(bits)

    session = db.get(models.Session, c.session_id)
    bug = models.Bug(
        session_id=c.session_id, charter_id=session.charter_id, severity="medium",
        title=f"On {wrong['name']}, {cmp.headline(step['differences'], wrong['name'])} after {step['description']}"[:200],
        description=(f"The same steps on {right['name']} ({right['url']}) and {wrong['name']} ({wrong['url']}) differ at "
                     f"step {n} ({step['description']}):\n" + "\n".join(f"- {d}" for d in step["differences"])
                     + "\n\nFound by comparing the two with Mirmera. A draft: review it before filing."),
        expected=f"As on {right['name']}: {seen(rs)}."[:2000], actual=f"On {wrong['name']}: {seen(ws)}."[:2000],
        steps_to_reproduce=[f"Use {wrong['name']} ({wrong['url']})"] + [describe_step(s) for s in (edited or steps)],
        edited_steps=edited, oracle_heuristic="comparable_products",
        oracle_explanation=f"{right['name']}, doing the same steps, behaves differently: {right['url']}",
        environment={"url": wrong["url"], "compared_with": f"{right['name']} {right['url']}", "found_by": "Mirmera comparison"},
        symptom=symptom, reproduction_trail=[{"tag": "test", "content": c.summary or ""}], sightings=[],
    )
    db.add(bug)
    db.flush()
    for name in step.get("shots", []):
        path = runner_api.session_folder(c.session_id) / name
        if path.is_file():
            db.add(models.Evidence(session_id=c.session_id, bug_id=bug.id, kind="screenshot", file_path=str(path),
                                   summary=f"Step {n} on {c.env_a['name'] if name.endswith('a.jpg') else c.env_b['name']}"))
    c.bug_ids = list(c.bug_ids or []) + [bug.id]
    db.commit()
    db.refresh(c)
    return c
