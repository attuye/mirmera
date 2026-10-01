from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai.base import get_provider, ProductContext
from app.database import get_db

router = APIRouter(tags=["charters"])


@router.post("/products/{product_id}/charters/suggest", response_model=list[schemas.CharterSuggestion])
async def suggest_charters(product_id: str, payload: schemas.CharterSuggestRequest, db: DBSession = Depends(get_db)):
    product = db.get(models.Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")

    artifacts = (
        db.query(models.Artifact)
        .filter(models.Artifact.id.in_(payload.artifact_ids), models.Artifact.product_id == product_id)
        .all()
    )
    if not artifacts:
        raise HTTPException(400, "No matching artifacts found for this product")

    artifact_context = "\n\n".join(
        f"[{a.type}] {a.label}\n{a.ai_summary or a.raw_content or ''}" for a in artifacts
    )
    recent = (
        db.query(models.Charter)
        .filter(models.Charter.product_id == product_id)
        .order_by(models.Charter.created_at.desc())
        .limit(10)
        .all()
    )
    recent_charters = "\n".join(f"- {c.mission_statement}" for c in recent) or "(none)"

    provider_name = payload.provider or product.default_ai_provider
    provider = get_provider(provider_name)

    context = ProductContext(
        project_name=product.name,
        artifact_context=artifact_context,
        recent_charters=recent_charters,
    )
    try:
        return await provider.suggest_charters(context)
    except NotImplementedError:
        raise HTTPException(501, f"Provider '{provider_name}' isn't implemented yet")
    except Exception as e:
        raise HTTPException(502, f"AI provider call failed: {e}")


@router.post("/products/{product_id}/charters", response_model=schemas.CharterOut)
def save_charter(product_id: str, payload: schemas.CharterCreate, db: DBSession = Depends(get_db)):
    product = db.get(models.Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")

    charter = models.Charter(product_id=product_id, **payload.model_dump())
    db.add(charter)
    db.commit()
    db.refresh(charter)
    return charter


@router.get("/products/{product_id}/charters", response_model=list[schemas.CharterOut])
def list_charters(product_id: str, db: DBSession = Depends(get_db)):
    return db.query(models.Charter).filter(models.Charter.product_id == product_id).all()

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
