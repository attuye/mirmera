from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai import tasks
from app.ai.base import get_provider
from app.database import get_db

router = APIRouter(tags=["charters"])


@router.post("/projects/{project_id}/charters/suggest", response_model=list[schemas.CharterSuggestion])
async def suggest_charters(project_id: str, payload: schemas.CharterSuggestRequest, db: DBSession = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    artifacts = (
        db.query(models.Artifact)
        .filter(models.Artifact.id.in_(payload.artifact_ids), models.Artifact.project_id == project_id)
        .all()
    )
    if not artifacts:
        raise HTTPException(400, "No matching artifacts found for this project")

    artifact_context = "\n\n".join(
        f"[{a.type}] {a.label}\n{a.ai_summary or a.raw_content or ''}" for a in artifacts
    )
    recent = (
        db.query(models.Charter)
        .filter(models.Charter.project_id == project_id)
        .order_by(models.Charter.created_at.desc())
        .limit(10)
        .all()
    )
    recent_charters = "\n".join(f"- {c.mission_statement}" for c in recent) or "(none)"
    
    # Provider problems (unknown name, missing key, model down) are turned into
    # clear HTTP errors by the handlers in app/main.py
    provider = get_provider(payload.provider or project.default_ai_provider)
    return await tasks.suggest_charters(
        provider,
        project_name=project.name, 
        artifact_context=artifact_context,
        recent_charters=recent_charters,
    )


@router.post("/projects/{project_id}/charters", response_model=schemas.CharterOut)
def save_charter(project_id: str, payload: schemas.CharterCreate, db: DBSession = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    data = payload.model_dump()
    risk_ids = data.pop("risk_ids")
    risks = []
    if risk_ids:
        risks = (
            db.query(models.Risk)
            .filter(models.Risk.id.in_(risk_ids), models.Risk.project_id == project_id)
            .all()
        )
        missing = set(risk_ids) - {r.id for r in risks}
        if missing:
            raise HTTPException(400, f"Unknown risk ids for this project: {sorted(missing)}")

    charter = models.Charter(project_id=project_id, **data)
    charter.risks = risks
    db.add(charter)
    db.commit()
    db.refresh(charter)
    return charter


@router.get("/projects/{project_id}/charters", response_model=list[schemas.CharterOut])
def list_charters(project_id: str, db: DBSession = Depends(get_db)):
    return db.query(models.Charter).filter(models.Charter.project_id == project_id).all()

@router.get("/charters/{charter_id}", response_model=schemas.CharterOut)
def get_charter(charter_id: str, db: DBSession = Depends(get_db)):
    charter = db.get(models.Charter, charter_id)
    if not charter:
        raise HTTPException(404, "Charter not found")
    return charter

@router.get("/charters/{charter_id}/active-session", response_model=schemas.SessionOut)
def get_active_session(charter_id: str, db: DBSession = Depends(get_db)):
    session = (
        db.query(models.Session)
        .filter(models.Session.charter_id == charter_id, models.Session.ended_at.is_(None))
        .order_by(models.Session.started_at.desc())
        .first()
    )
    if not session:
        raise HTTPException(404, "No active session for this charter")
    return session
