"""OpenAPI specs of a project: import (an address or pasted text), the endpoints with a ready
request each, and which endpoints the project's sessions have tried."""
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import api_explore, models, openapi, schemas
from app.config import settings
from app.database import get_db

router = APIRouter(tags=["openapi"])


def _project(db: DBSession, project_id: str) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _spec(db: DBSession, project_id: str, artifact_id: str) -> models.Artifact:
    a = db.get(models.Artifact, artifact_id)
    if not a or a.project_id != project_id or a.type != "openapi":
        raise HTTPException(404, "OpenAPI spec not found")
    return a


def _fetch(url: str) -> str:
    try:
        api_explore.check_target(url)
    except api_explore.ApiProblem as err:
        raise HTTPException(422, str(err))
    try:
        with httpx.Client(timeout=settings.api_timeout_seconds, follow_redirects=True) as client:
            with client.stream("GET", url, headers={"Accept": "application/json, application/yaml, text/yaml, */*"}) as r:
                if r.status_code >= 400:
                    raise HTTPException(422, f"{url} answered {r.status_code}.")
                raw = b""
                for chunk in r.iter_bytes():
                    raw += chunk
                    if len(raw) > openapi.MAX_SPEC_BYTES:
                        raise HTTPException(422, "The spec is larger than 5 MB.")
                return raw.decode(r.encoding or "utf-8", errors="replace")
    except httpx.HTTPError as err:
        raise HTTPException(422, f"Couldn't get {url}: {str(err) or type(err).__name__}")


def _summary(a: models.Artifact) -> dict:
    info, ops, servers = openapi.parsed(a.raw_content)
    return {"id": a.id, "label": a.label, "title": info["title"], "version": info["version"], "servers": servers,
            "operations": len(ops), "source": a.file_ref, "imported_at": a.created_at}


def _save(db: DBSession, project_id: str, text: str, source: str | None) -> models.Artifact:
    try:
        info, _, _ = openapi.parsed(text)
    except openapi.SpecProblem as err:
        raise HTTPException(422, str(err))
    label = f"OpenAPI: {info['title']} {info['version']}".strip()[:200]
    existing = [a for a in db.query(models.Artifact).filter(models.Artifact.project_id == project_id,
                                                             models.Artifact.type == "openapi")
                if openapi.parsed(a.raw_content)[0]["title"] == info["title"]]
    a = existing[0] if existing else models.Artifact(project_id=project_id, type="openapi")
    a.label, a.raw_content, a.file_ref, a.ai_summary = label, text, source, openapi.endpoint_list(text)
    if not existing:
        db.add(a)
    db.commit()
    db.refresh(a)
    return a


@router.post("/projects/{project_id}/openapi", response_model=schemas.OpenApiSpecOut)
def import_spec(project_id: str, payload: schemas.OpenApiImport, db: DBSession = Depends(get_db)):
    """From an address (fetched by the server) or pasted text (JSON or YAML). The same API
    imported again (same title) replaces the earlier one."""
    _project(db, project_id)
    if bool(payload.url) == bool(payload.text):
        raise HTTPException(422, "Give either the spec's address or its text.")
    text = _fetch(payload.url.strip()) if payload.url else payload.text
    return _summary(_save(db, project_id, text, payload.url.strip() if payload.url else None))


@router.get("/projects/{project_id}/openapi", response_model=list[schemas.OpenApiSpecOut])
def list_specs(project_id: str, db: DBSession = Depends(get_db)):
    _project(db, project_id)
    rows = db.query(models.Artifact).filter(models.Artifact.project_id == project_id, models.Artifact.type == "openapi") \
        .order_by(models.Artifact.created_at).all()
    return [_summary(a) for a in rows]


@router.get("/projects/{project_id}/openapi/{artifact_id}", response_model=schemas.OpenApiOperationsOut)
def spec_operations(project_id: str, artifact_id: str, db: DBSession = Depends(get_db)):
    """Every endpoint with a ready request, and how often this project's sessions sent it."""
    a = _spec(db, project_id, artifact_id)
    _, ops, _ = openapi.parsed(a.raw_content)
    calls = (db.query(models.Evidence).join(models.Session).join(models.Charter)
             .filter(models.Charter.project_id == project_id, models.Evidence.kind == "api_call")
             .order_by(models.Evidence.captured_at).all())
    sent = [((c.data or {}).get("method"), urlsplit(c.url or "").path, (c.data or {}).get("status")) for c in calls]
    out = []
    for op in ops:
        pattern = openapi.path_pattern(op["path"])
        hits = [s for m, p, s in sent if m == op["method"] and pattern.search(p)]
        out.append({**op, "tried": len(hits), "last_status": hits[-1] if hits else None})
    return {**_summary(a), "endpoints": out, "tried": sum(1 for o in out if o["tried"])}


@router.post("/projects/{project_id}/openapi/{artifact_id}/refresh", response_model=schemas.OpenApiSpecOut)
def refresh_spec(project_id: str, artifact_id: str, db: DBSession = Depends(get_db)):
    a = _spec(db, project_id, artifact_id)
    if not (a.file_ref or "").startswith(("http://", "https://")):
        raise HTTPException(422, "This spec was pasted, not imported from an address: paste the new version instead.")
    return _summary(_save(db, project_id, _fetch(a.file_ref), a.file_ref))


@router.delete("/projects/{project_id}/openapi/{artifact_id}", status_code=204)
def delete_spec(project_id: str, artifact_id: str, db: DBSession = Depends(get_db)):
    """Charters suggested from it keep their text; they just stop pointing at it."""
    a = _spec(db, project_id, artifact_id)
    db.query(models.Charter).filter(models.Charter.source_artifact_id == a.id).update({"source_artifact_id": None})
    db.query(models.Story).filter(models.Story.artifact_id == a.id).update({"artifact_id": None})
    db.delete(a)
    db.commit()
