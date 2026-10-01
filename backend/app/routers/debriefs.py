from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/sessions/{session_id}/debrief", tags=["debriefs"])


@router.get("", response_model=schemas.DebriefOut)
def get_debrief(session_id: str, db: DBSession = Depends(get_db)):
    debrief = db.query(models.Debrief).filter(models.Debrief.session_id == session_id).first()
    if not debrief:
        raise HTTPException(404, "Debrief not found — has this session ended?")
    return debrief


@router.patch("", response_model=schemas.DebriefOut)
def update_debrief(session_id: str, payload: schemas.DebriefUpdate, db: DBSession = Depends(get_db)):
    debrief = db.query(models.Debrief).filter(models.Debrief.session_id == session_id).first()
    if not debrief:
        raise HTTPException(404, "Debrief not found")
    debrief.final_text = payload.final_text
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
