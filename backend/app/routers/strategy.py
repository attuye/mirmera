"""The test strategy of a project (or of a sprint): what Mirmera knows per section, an optional
AI draft in prose, saved versions, and exports."""
import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy.orm import Session as DBSession

from app import auth, models, schemas, strategy
from app.ai import tasks
from app.ai.routing import provider_for
from app.database import get_db

router = APIRouter(tags=["test strategy"])


def _project(db: DBSession, project_id: str) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _sprint(db: DBSession, project: models.Project, sprint_id: str | None) -> models.Sprint | None:
    if not sprint_id:
        return None
    sprint = db.get(models.Sprint, sprint_id)
    if not sprint or sprint.project_id != project.id:
        raise HTTPException(404, "Sprint not found")
    return sprint


def _out(row: models.TestStrategy) -> dict:
    return {"id": row.id, "version": row.version, "sprint_id": row.sprint_id, "sprint_name": row.sprint.name if row.sprint else None,
            "sections": row.sections, "ai_provider": row.ai_provider, "ai_model": row.ai_model,
            "author": row.author.name if row.author else None, "created_at": row.created_at}


def _versions(db: DBSession, project_id: str, sprint_id: str | None) -> list[models.TestStrategy]:
    q = db.query(models.TestStrategy).filter(models.TestStrategy.project_id == project_id)
    q = q.filter(models.TestStrategy.sprint_id == sprint_id) if sprint_id else q.filter(models.TestStrategy.sprint_id.is_(None))
    return q.order_by(models.TestStrategy.version.desc()).all()


@router.get("/projects/{project_id}/strategy", response_model=schemas.StrategyOverview)
def overview(project_id: str, sprint_id: str | None = None, db: DBSession = Depends(get_db)):
    """The sections, saved versions for this focus (newest first), and a draft from the facts."""
    project = _project(db, project_id)
    f = strategy.facts(db, project, _sprint(db, project, sprint_id))
    return {"sections": strategy.SECTIONS, "versions": [_out(v) for v in _versions(db, project_id, sprint_id)],
            "draft": strategy.draft(f), "facts": f}


@router.post("/projects/{project_id}/strategy/ai-draft", response_model=schemas.StrategyAIDraft)
async def ai_draft(project_id: str, payload: schemas.StrategyAIRequest, db: DBSession = Depends(get_db)):
    """Prose for every section from the facts; the tester keeps, edits or ignores it."""
    project = _project(db, project_id)
    sprint = _sprint(db, project, payload.sprint_id)
    f = strategy.facts(db, project, sprint)
    provider = provider_for(project, "test_strategy", payload.provider)
    result = await tasks.write_strategy(provider, project_name=project.name,
                                        focus=f"{sprint.name}" + (f" ({sprint.goal})" if sprint.goal else "") if sprint else "the whole project",
                                        facts=f)
    return {"sections": result.sections, "ai_provider": provider.name, "ai_model": provider.model,
            "prompt_version": tasks.PROMPT_VERSIONS["test_strategy"]}


@router.post("/projects/{project_id}/strategy", response_model=schemas.StrategyVersionOut)
def save(project_id: str, payload: schemas.StrategySave, request: Request, db: DBSession = Depends(get_db)):
    """A new version for this focus; earlier versions stay."""
    project = _project(db, project_id)
    _sprint(db, project, payload.sprint_id)
    unknown = sorted(set(payload.sections) - set(strategy.KEYS))
    if unknown:
        raise HTTPException(400, f"Unknown sections: {', '.join(unknown)}")
    sections = {k: (payload.sections.get(k) or "").strip()[:20_000] for k in strategy.KEYS}
    if not any(sections.values()):
        raise HTTPException(422, "The strategy is empty.")
    latest = _versions(db, project_id, payload.sprint_id)
    row = models.TestStrategy(project_id=project_id, sprint_id=payload.sprint_id, version=(latest[0].version + 1) if latest else 1,
                              sections=sections, ai_provider=payload.ai_provider, ai_model=payload.ai_model,
                              prompt_version=payload.prompt_version, created_by=auth.current_user(request, db).id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row)


def _version(db: DBSession, project_id: str, version_id: str) -> models.TestStrategy:
    row = db.get(models.TestStrategy, version_id)
    if not row or row.project_id != project_id:
        raise HTTPException(404, "Strategy version not found")
    return row


@router.get("/projects/{project_id}/strategy/{version_id}/export")
def export(project_id: str, version_id: str, format: str = "md", db: DBSession = Depends(get_db)):
    """Markdown to download, or a page to print."""
    project = _project(db, project_id)
    row = _version(db, project_id, version_id)
    text = strategy.markdown(project, row)
    name = f"test-strategy-v{row.version}.md"
    if format == "md":
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})
    if format != "html":
        raise HTTPException(400, "Use format=md or format=html")
    body = []
    for line in text.splitlines():
        if line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            body.append(f"<li>{html.escape(line[2:])}</li>")
        elif line.strip():
            body.append(f"<p>{html.escape(line)}</p>")
    page = ("<!doctype html><meta charset='utf-8'><title>Test strategy</title>"
            "<style>body{font:14px/1.55 system-ui,sans-serif;max-width:760px;margin:40px auto;color:#111}"
            "h1{font-size:22px}h2{font-size:16px;margin-top:26px;border-bottom:1px solid #ddd;padding-bottom:4px}"
            "li{margin-left:18px}@media print{body{margin:0}}</style>" + "\n".join(body))
    return HTMLResponse(page)
