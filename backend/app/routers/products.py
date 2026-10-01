from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai.base import PROVIDER_NAMES
from app.database import get_db

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", response_model=schemas.ProductOut)
def create_product(payload: schemas.ProductCreate, db: DBSession = Depends(get_db)):
    product = models.Product(name=payload.name, org_id=payload.org_id)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get("", response_model=list[schemas.ProductOut])
def list_products(db: DBSession = Depends(get_db)):
    return db.query(models.Product).all()


@router.get("/{product_id}", response_model=schemas.ProductOut)
def get_product(product_id: str, db: DBSession = Depends(get_db)):
    product = db.get(models.Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    return product


@router.patch("/{product_id}", response_model=schemas.ProductOut)
def update_product(product_id: str, payload: schemas.ProductUpdate, db: DBSession = Depends(get_db)):
    """Rename a product, set its testing mission, switch its AI provider, or
    set where its bugs are filed (GitHub repo, Jira project).
    (A product keeps the provider it was created with until changed here.)"""
    product = db.get(models.Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    changes = payload.model_dump(exclude_unset=True)
    provider = changes.get("default_ai_provider")
    if provider is not None and provider not in PROVIDER_NAMES:
        raise HTTPException(400, f"Unknown AI provider '{provider}'. Use one of: {', '.join(PROVIDER_NAMES)}")
    for field in ("github_repo", "jira_project_key"):
        if field in changes:
            changes[field] = (changes[field] or "").strip() or None  # "" clears the target
    for field, value in changes.items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product
