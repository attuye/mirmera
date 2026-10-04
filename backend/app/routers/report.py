"""A project's test report: as data for the Report page, as Markdown, as a printable page."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session as DBSession

from app import models, project_report, schemas
from app.ai import tasks
from app.ai.routing import provider_for
from app.database import get_db
from app.regression.plan import slug

router = APIRouter(tags=["report"])


def _report(db: DBSession, project_id: str, since: date | None, until: date | None) -> dict:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if since and until and since > until:
        raise HTTPException(422, "The period starts after it ends.")
    report = project_report.build(db, project, since, until)
    kept = _kept(db, project_id, since, until)
    report["ai_summary"] = schemas.ReportSummaryOut.model_validate(kept).model_dump() if kept else None
    return report


def _key(since: date | None, until: date | None) -> tuple[str, str]:
    return (str(since) if since else "", str(until) if until else "")


def _kept(db: DBSession, project_id: str, since: date | None, until: date | None) -> models.ReportSummary | None:
    s, u = _key(since, until)
    return (db.query(models.ReportSummary).filter_by(project_id=project_id, since=s, until=u)
            .order_by(models.ReportSummary.created_at.desc()).first())


def _filename(report: dict, ext: str, view: str = "full") -> str:
    span = f"-{report['since'] or 'start'}-to-{report['until'] or report['generated_at']:%Y-%m-%d}" if report["since"] or report["until"] else ""
    kind = "-summary" if view == "summary" else ""
    return f"{slug(report['project_name'])}-test-report{kind}{span}.{ext}"


@router.get("/projects/{project_id}/report", response_model=schemas.ProjectReport)
def get_report(project_id: str, since: date | None = None, until: date | None = None, db: DBSession = Depends(get_db)):
    """since / until: whole days (UTC), both included; leave out for all time."""
    return _report(db, project_id, since, until)


@router.get("/projects/{project_id}/report.md")
def report_markdown(project_id: str, since: date | None = None, until: date | None = None,
                    view: Literal["summary", "full"] = "full", db: DBSession = Depends(get_db)):
    """view=summary: at a glance, charts and top bugs only."""
    report = _report(db, project_id, since, until)
    return Response(project_report.to_markdown(report, view), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(report, "md", view)}"'})


@router.get("/projects/{project_id}/report.html", response_class=HTMLResponse)
def report_html(project_id: str, since: date | None = None, until: date | None = None,
                view: Literal["summary", "full"] = "full", db: DBSession = Depends(get_db)):
    """Opens in the browser, ready to print or save as PDF."""
    return HTMLResponse(project_report.to_html(_report(db, project_id, since, until), view))


@router.post("/projects/{project_id}/report/summary", response_model=schemas.ReportSummaryDraft)
async def draft_summary(project_id: str, payload: schemas.ReportSummaryRequest, since: date | None = None,
                        until: date | None = None, db: DBSession = Depends(get_db)):
    """An AI draft of the report's opening summary, from the report's own facts only. Not saved:
    the tester keeps it (PUT), edits it, or throws it away."""
    report = _report(db, project_id, since, until)
    project = db.get(models.Project, project_id)
    provider = provider_for(project, "report_summary", payload.provider)
    result = await tasks.summarize_report(provider, project_name=project.name, facts=project_report.facts(report))
    return schemas.ReportSummaryDraft(text=result.summary, basis=result.basis, ai_provider=provider.name,
                                      ai_model=provider.model, prompt_version=tasks.PROMPT_VERSIONS["report_summary"])


@router.put("/projects/{project_id}/report/summary", response_model=schemas.ReportSummaryOut)
def keep_summary(project_id: str, payload: schemas.ReportSummaryIn, since: date | None = None,
                 until: date | None = None, db: DBSession = Depends(get_db)):
    """Keep a summary for this period (replacing one kept before); exports show it, labelled."""
    _report(db, project_id, since, until)  # the project exists and the period is valid
    s, u = _key(since, until)
    db.query(models.ReportSummary).filter_by(project_id=project_id, since=s, until=u).delete()
    kept = models.ReportSummary(project_id=project_id, since=s, until=u, text=payload.text.strip(), edited=payload.edited,
                                ai_provider=payload.ai_provider, ai_model=payload.ai_model, prompt_version=payload.prompt_version)
    db.add(kept)
    db.commit()
    db.refresh(kept)
    return kept


@router.delete("/projects/{project_id}/report/summary", status_code=204)
def remove_summary(project_id: str, since: date | None = None, until: date | None = None, db: DBSession = Depends(get_db)):
    s, u = _key(since, until)
    db.query(models.ReportSummary).filter_by(project_id=project_id, since=s, until=u).delete()
    db.commit()
