from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.database import get_db
from app.routers.risks import _next_ref_number

router = APIRouter(prefix="/sessions/{session_id}/debrief", tags=["debriefs"])


@router.get("", response_model=schemas.DebriefOut)
def get_debrief(session_id: str, db: DBSession = Depends(get_db)):
    debrief = db.query(models.Debrief).filter(models.Debrief.session_id == session_id).first()
    if not debrief:
        raise HTTPException(404, "Debrief not found — has this session ended?")
    return debrief


def _debrief_or_404(db: DBSession, session_id: str) -> models.Debrief:
    debrief = db.query(models.Debrief).filter(models.Debrief.session_id == session_id).first()
    if not debrief:
        raise HTTPException(404, "Debrief not found")
    return debrief


@router.patch("", response_model=schemas.DebriefOut)
def update_debrief(session_id: str, payload: schemas.DebriefUpdate, db: DBSession = Depends(get_db)):
    debrief = _debrief_or_404(db, session_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(debrief, field, value)
    db.commit()
    db.refresh(debrief)
    return debrief


@router.post("/outlook/{index}/charter", response_model=schemas.DebriefOut)
def save_outlook_charter(session_id: str, index: int, db: DBSession = Depends(get_db)):
    """Save a suggested follow-up charter. It investigates the same risks as the
    session's own charter, and can only be saved once."""
    debrief = _debrief_or_404(db, session_id)
    outlook = [dict(o) for o in (debrief.outlook or [])]
    if not 0 <= index < len(outlook):
        raise HTTPException(404, "No such follow-up charter")
    if outlook[index].get("charter_id"):
        raise HTTPException(409, "This follow-up charter is already saved")

    original = debrief.session.charter
    charter = models.Charter(
        product_id=original.product_id,
        mission_statement=outlook[index]["charter"],
        rationale=outlook[index]["reason"],
        priority=original.priority,
        source="follow-up",
    )
    charter.risks = list(original.risks)
    db.add(charter)
    db.flush()
    outlook[index]["charter_id"] = charter.id
    debrief.outlook = outlook  # reassign: JSON columns don't notice in-place edits
    db.commit()
    db.refresh(debrief)
    return debrief


@router.post("/risks-observed/{index}/risk", response_model=schemas.DebriefOut)
def promote_observed_risk(
    session_id: str, index: int, payload: schemas.ObservedRiskPromotion, db: DBSession = Depends(get_db),
):
    """Turn a risk observed during the session into an accepted risk. Once only."""
    debrief = _debrief_or_404(db, session_id)
    observed = [dict(r) for r in (debrief.risks_observed or [])]
    if not 0 <= index < len(observed):
        raise HTTPException(404, "No such observed risk")
    if observed[index].get("risk_id"):
        raise HTTPException(409, "This observed risk is already a risk")

    text = observed[index]["text"]
    product_id = debrief.session.charter.product_id
    risk = models.Risk(
        product_id=product_id,
        ref=f"R{_next_ref_number(db, product_id)}",
        title=payload.title or text[:80],
        failure_mode=text,
        quality_criterion=payload.quality_criterion,
        impact=payload.impact,
        likelihood=payload.likelihood,
        rationale="Observed during an exploratory session.",
        evidence="tester",
        status="accepted",
        source="session",
        last_change="Promoted from a session debrief",
    )
    db.add(risk)
    db.flush()
    observed[index]["risk_id"] = risk.id
    debrief.risks_observed = observed
    db.commit()
    db.refresh(debrief)
    return debrief

# router = APIRouter(prefix="/sessions/{session_id}/debrief", tags=["debriefs"])
list_router = APIRouter(tags=["debriefs"])


@list_router.get("/products/{product_id}/debriefs", response_model=list[schemas.DebriefListItem])
def list_debriefs(product_id: str, db: DBSession = Depends(get_db)):
    rows = (
        db.query(models.Debrief, models.Session, models.Charter)
        .join(models.Session, models.Debrief.session_id == models.Session.id)
        .join(models.Charter, models.Session.charter_id == models.Charter.id)
        .filter(models.Charter.product_id == product_id)
        .order_by(models.Debrief.created_at.desc())
        .all()
    )
    return [
        schemas.DebriefListItem(
            id=d.id,
            session_id=d.session_id,
            charter_mission=c.mission_statement,
            ended_at=s.ended_at,
            coverage_summary=d.final_text or d.coverage_summary,
            created_at=d.created_at,
        )
        for d, s, c in rows
    ]
