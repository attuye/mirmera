"""The project's test data: list, add, change, delete, import a CSV, and the app's known fields."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app import test_data as td
from app.database import get_db

router = APIRouter(tags=["test data"])
TRUE = {"1", "yes", "true", "y", "once", "secret"}


def _out(t: models.TestData) -> schemas.TestDataOut:
    out = schemas.TestDataOut.model_validate(t)
    out.value = None if t.secret else t.value
    out.not_seen = bool(t.missed_at and (not t.last_seen_at or t.missed_at > t.last_seen_at))
    return out


def _project(db: DBSession, project_id: str) -> models.Project:
    p = db.get(models.Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


def _unique_name(db: DBSession, project_id: str, name: str, but: str | None = None) -> None:
    q = db.query(models.TestData).filter(models.TestData.project_id == project_id)
    if any(t.name.lower() == name.lower() and t.id != but for t in q):
        raise HTTPException(409, f'There is already test data named "{name}": names are how steps refer to it ({{data:{name}}}).')


@router.get("/projects/{project_id}/test-data", response_model=list[schemas.TestDataOut])
def listed(project_id: str, db: DBSession = Depends(get_db)):
    _project(db, project_id)
    rows = db.query(models.TestData).filter(models.TestData.project_id == project_id).order_by(models.TestData.created_at)
    return [_out(t) for t in rows]


@router.post("/projects/{project_id}/test-data", response_model=schemas.TestDataOut)
def add(project_id: str, payload: schemas.TestDataIn, db: DBSession = Depends(get_db)):
    _project(db, project_id)
    if not payload.value:
        raise HTTPException(422, "Give the value.")
    _unique_name(db, project_id, payload.name)
    t = models.TestData(project_id=project_id, **payload.model_dump(exclude={"value", "secret"}))
    td.save_value(t, payload.value, payload.secret)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _out(t)


@router.patch("/projects/{project_id}/test-data/{row_id}", response_model=schemas.TestDataOut)
def change(project_id: str, row_id: str, payload: schemas.TestDataIn, db: DBSession = Depends(get_db)):
    t = db.get(models.TestData, row_id)
    if not t or t.project_id != project_id:
        raise HTTPException(404, "Not found")
    _unique_name(db, project_id, payload.name, but=t.id)
    for k, v in payload.model_dump(exclude={"value", "secret"}).items():
        setattr(t, k, v)
    if payload.value is not None or payload.secret != t.secret:
        value = payload.value if payload.value is not None else td.to_row(t)["value"]
        td.save_value(t, value, payload.secret)
    db.commit()
    db.refresh(t)
    return _out(t)


@router.post("/projects/{project_id}/test-data/{row_id}/reset", response_model=schemas.TestDataOut)
def reset(project_id: str, row_id: str, db: DBSession = Depends(get_db)):
    """A "once" value usable again (e.g. the test database was reset)."""
    t = db.get(models.TestData, row_id)
    if not t or t.project_id != project_id:
        raise HTTPException(404, "Not found")
    t.used_at = t.used_by_run = None
    db.commit()
    db.refresh(t)
    return _out(t)


@router.delete("/projects/{project_id}/test-data/{row_id}", status_code=204)
def delete(project_id: str, row_id: str, db: DBSession = Depends(get_db)):
    t = db.get(models.TestData, row_id)
    if not t or t.project_id != project_id:
        raise HTTPException(404, "Not found")
    db.delete(t)
    db.commit()


@router.post("/projects/{project_id}/test-data/import", response_model=list[schemas.TestDataOut])
def import_csv(project_id: str, payload: schemas.TestDataImport, db: DBSession = Depends(get_db)):
    """Adds every row of a CSV (header: name,value,labels,kind,once,secret,page,note; labels joined
    with |). Nothing is added if a row is wrong: the error names it."""
    _project(db, project_id)
    reader = csv.DictReader(io.StringIO(payload.csv.strip()))
    if not reader.fieldnames or not {"name", "value"} <= {h.strip().lower() for h in reader.fieldnames}:
        raise HTTPException(422, "The first line must name the columns, with at least: name,value")
    made, names = [], set()
    for n, raw in enumerate(reader, 2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        try:
            item = schemas.TestDataIn(
                name=row.get("name", ""), value=row.get("value", ""), labels=[x for x in row.get("labels", "").split("|")],
                kind=row.get("kind") or "valid", once=row.get("once", "").lower() in TRUE,
                secret=row.get("secret", "").lower() in TRUE, page=row.get("page") or None, note=row.get("note") or None)
        except Exception as err:
            raise HTTPException(422, f"Line {n}: {str(err).splitlines()[-1] if str(err) else err}") from err
        if not item.value:
            raise HTTPException(422, f"Line {n}: no value")
        if item.name.lower() in names:
            raise HTTPException(422, f'Line {n}: "{item.name}" is named twice')
        names.add(item.name.lower())
        _unique_name(db, project_id, item.name)
        t = models.TestData(project_id=project_id, **item.model_dump(exclude={"value", "secret"}))
        td.save_value(t, item.value, item.secret)
        made.append(t)
    db.add_all(made)
    db.commit()
    return [_out(t) for t in made]


@router.get("/projects/{project_id}/known-fields", response_model=list[schemas.KnownField])
def known(project_id: str, db: DBSession = Depends(get_db)):
    """The app's fields as Mirmera has met them (the survey, and what runs filled): to pick from."""
    _project(db, project_id)
    return td.known_fields(db, project_id)
