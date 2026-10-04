"""A project's test report: as data for the Report page, as Markdown, as a printable page."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session as DBSession

from app import models, project_report, schemas
from app.database import get_db
from app.regression.plan import slug

router = APIRouter(tags=["report"])


def _report(db: DBSession, project_id: str, since: date | None, until: date | None) -> dict:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if since and until and since > until:
        raise HTTPException(422, "The period starts after it ends.")
    return project_report.build(db, project, since, until)


def _filename(report: dict, ext: str) -> str:
    span = f"-{report['since'] or 'start'}-to-{report['until'] or report['generated_at']:%Y-%m-%d}" if report["since"] or report["until"] else ""
    return f"{slug(report['project_name'])}-test-report{span}.{ext}"


@router.get("/projects/{project_id}/report", response_model=schemas.ProjectReport)
def get_report(project_id: str, since: date | None = None, until: date | None = None, db: DBSession = Depends(get_db)):
    """since / until: whole days (UTC), both included; leave out for all time."""
    return _report(db, project_id, since, until)


@router.get("/projects/{project_id}/report.md")
def report_markdown(project_id: str, since: date | None = None, until: date | None = None, db: DBSession = Depends(get_db)):
    report = _report(db, project_id, since, until)
    return Response(project_report.to_markdown(report), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(report, "md")}"'})


@router.get("/projects/{project_id}/report.html", response_class=HTMLResponse)
def report_html(project_id: str, since: date | None = None, until: date | None = None, db: DBSession = Depends(get_db)):
    """Opens in the browser, ready to print or save as PDF."""
    return HTMLResponse(project_report.to_html(_report(db, project_id, since, until)))
