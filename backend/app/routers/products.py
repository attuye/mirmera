from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
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
