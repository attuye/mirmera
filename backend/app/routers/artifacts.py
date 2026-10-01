import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai import tasks
from app.ai.base import ProviderError, get_provider
from app.database import get_db

logger = logging.getLogger(__name__)

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
    # A provider problem (missing key, model down) must never block saving the artifact:
    # the summary stays empty and charter suggestion falls back to the raw content.
    # The reason is logged, so a misconfiguration doesn't go unnoticed.
    if artifact.raw_content:
        try:
            provider = get_provider(product.default_ai_provider)
            artifact.ai_summary = await tasks.summarize_artifact(
                provider, raw_content=artifact.raw_content, artifact_type=artifact.type,
            )
            db.commit()
            db.refresh(artifact)
        except ProviderError as err:
            logger.warning("Artifact %s saved without AI summary: %s", artifact.id, err)

    return artifact


@router.get("", response_model=list[schemas.ArtifactOut])
def list_artifacts(product_id: str, db: DBSession = Depends(get_db)):
    return db.query(models.Artifact).filter(models.Artifact.product_id == product_id).all()
