"""API exploration in a session: send requests (typed, from curl, replayed from the recorded
browser) and keep each one as evidence of the session."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import api_explore, models, schemas
from app.database import get_db
from app.timeutil import utcnow

router = APIRouter(tags=["api exploration"])


def _session(db: DBSession, session_id: str, running: bool = False) -> models.Session:
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if running and session.ended_at:
        raise HTTPException(409, "This session has ended. Start a new one to explore more.")
    return session


@router.post("/sessions/{session_id}/api/send", response_model=schemas.ApiSendOut)
def send(session_id: str, payload: schemas.ApiRequestIn, db: DBSession = Depends(get_db)):
    """Send once from the server and keep it (redacted) as evidence. A path like /api/x is
    taken on the given base address, or the project's app URL."""
    session = _session(db, session_id, running=True)
    base = payload.base_url or session.charter.project.app_url
    url = api_explore.resolve(payload.url, base)
    headers = [h.model_dump() for h in payload.headers]
    try:
        result = api_explore.send(payload.method, url, headers, payload.body)
    except api_explore.ApiProblem as err:
        raise HTTPException(422, str(err))
    fields = api_explore.evidence_fields(payload.method, url, headers, payload.body, result, payload.source)
    now = utcnow()
    evidence = models.Evidence(session_id=session_id, captured_at=now, last_seen_at=now, count=1, **fields)
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    return {"evidence": evidence, "response": {**result, "url": url}}


@router.post("/sessions/{session_id}/api/parse-curl", response_model=schemas.ApiRequestDraft)
def parse_curl(session_id: str, payload: schemas.CurlIn, db: DBSession = Depends(get_db)):
    _session(db, session_id)
    try:
        return api_explore.parse_curl(payload.command)
    except api_explore.ApiProblem as err:
        raise HTTPException(422, str(err))


@router.get("/sessions/{session_id}/api/recorded", response_model=list[schemas.RecordedRequest])
def recorded(session_id: str, db: DBSession = Depends(get_db)):
    """Requests the recorded browser (or the extension) saw fail or go slow, to replay and vary."""
    _session(db, session_id)
    rows = db.query(models.Evidence).filter(models.Evidence.session_id == session_id,
                                            models.Evidence.kind.in_(("failed_request", "slow_request"))).all()
    return api_explore.recorded_requests(rows)


@router.get("/sessions/{session_id}/api/history", response_model=list[schemas.EvidenceOut])
def history(session_id: str, db: DBSession = Depends(get_db)):
    """The requests sent in this session, newest first."""
    _session(db, session_id)
    return (db.query(models.Evidence).filter(models.Evidence.session_id == session_id, models.Evidence.kind == "api_call")
            .order_by(models.Evidence.captured_at.desc()).limit(100).all())
