from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai.base import get_provider
from app.database import get_db

router = APIRouter(prefix="/products/{product_id}/artifacts", tags=["artifacts"])


@router.post("", response_model=schemas.ArtifactOut)
async def add_artifact(product_id: str, payload: schemas.ArtifactCreate, db: DBSession = Depends(get_db)):
    product = db.get(models.Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")

    artifact = models.Artifact(product_id=product_id, **payload.model_dump())
    db.add(artifact)
    db.commit()
    db.refresh(artifact)

    # Cache an AI summary up front so charter suggestion doesn't re-summarize every call.
    # Never let a summary failure (missing API key, rate limit, network blip, unimplemented
    # provider stub) block saving the artifact itself — summary just stays null and charter
    # suggestion falls back to the raw content.
    if artifact.raw_content:
        provider = get_provider(product.default_ai_provider)
        try:
            artifact.ai_summary = await provider.summarize_artifact(artifact.raw_content, artifact.type)
            db.commit()
            db.refresh(artifact)
        except Exception:
            pass

    return artifact


@router.get("", response_model=list[schemas.ArtifactOut])
def list_artifacts(product_id: str, db: DBSession = Depends(get_db)):
    return db.query(models.Artifact).filter(models.Artifact.product_id == product_id).all()
