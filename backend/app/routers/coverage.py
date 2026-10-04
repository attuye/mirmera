"""The coverage map: pages and product areas the testing reached, and the gaps."""
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import coverage, models, schemas
from app.database import get_db

router = APIRouter(tags=["coverage"])


@router.get("/projects/{project_id}/coverage", response_model=schemas.CoverageMap)
def coverage_map(project_id: str, since: date | None = None, until: date | None = None, db: DBSession = Depends(get_db)):
    """since / until: only sessions started in these days (UTC, both included); leave out for all time."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return coverage.build(db, project, datetime.combine(since, time.min) if since else None,
                          datetime.combine(until + timedelta(days=1), time.min) if until else None)
