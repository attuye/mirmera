"""AI steps that survive a refresh.

Building the product model, proposing risks and drafting charters from risks each take
the AI a while (minutes with a local model). Run in the request, a refresh threw the
result away while the AI kept working. As a job, the step runs in the background, its
result is kept, and any page (after a refresh, in another tab, a colleague's) can see it
working and then show the result. Running jobs also show in Mirmera's activity.

Each kind runs the same code as its direct endpoint, so both behave the same.
"""
import logging
from datetime import timedelta

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai.base import ProviderError
from app.database import SessionLocal
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

ACTIVE: set[str] = set()  # job ids being worked on in this process
INTERRUPTED = "Interrupted: the backend stopped while the AI was working. Start it again."


def _kinds():
    from app.routers import risks
    return {
        "product_model": ("Build the product model", risks.build_product_model, schemas.ProductModelRequest),
        "risk_analysis": ("Propose risks", risks.analyze_risks, schemas.RiskAnalyzeRequest),
        "charters_from_risks": ("Draft charters from risks", risks.charters_from_risks, schemas.ChartersFromRisksRequest),
    }


def label(kind: str) -> str:
    return _kinds().get(kind, (kind,))[0]


def out(job: models.AiJob) -> schemas.AiJobOut:
    o = schemas.AiJobOut.model_validate(job)
    o.label = label(job.kind)
    return o


def settle(db: DBSession, job: models.AiJob | None) -> models.AiJob | None:
    """A job left "running" by a backend that stopped is shown as interrupted."""
    if job and job.status == "running" and job.id not in ACTIVE:
        job.status, job.error, job.finished_at = "failed", INTERRUPTED, utcnow()
        db.commit()
    return job


def validate(kind: str, request: dict):
    """The request as its endpoint would read it: a bad one is refused before any job starts."""
    return _kinds()[kind][2].model_validate(request)


def start(db: DBSession, project_id: str, kind: str, request: dict, started_by: str | None) -> tuple[models.AiJob, bool]:
    """(job, new). One job of a kind per project at a time: a second click gets the running one."""
    running = (db.query(models.AiJob)
               .filter(models.AiJob.project_id == project_id, models.AiJob.kind == kind, models.AiJob.status == "running")
               .order_by(models.AiJob.created_at.desc()).first())
    if settle(db, running) and running.status == "running":
        return running, False
    job = models.AiJob(project_id=project_id, kind=kind, request=request, started_by=started_by)
    db.add(job)
    db.commit()
    db.refresh(job)
    ACTIVE.add(job.id)
    return job, True


async def run(job_id: str) -> None:
    status, result, error = "failed", None, None
    try:
        with SessionLocal() as db:
            job = db.get(models.AiJob, job_id)
            _, fn, request_schema = _kinds()[job.kind]
            answer = await fn(job.project_id, request_schema.model_validate(job.request or {}), db)
            result, status = jsonable_encoder(answer), "done"
    except HTTPException as err:  # the step's own refusal, e.g. "Accept at least one risk first"
        error = str(err.detail)
    except ProviderError as err:
        error = f"The AI could not do it: {err}"
    except Exception as err:
        logger.exception("AI job %s failed", job_id)
        error = f"The AI step failed: {str(err).splitlines()[0] if str(err) else type(err).__name__}"
    finally:
        with SessionLocal() as db:
            job = db.get(models.AiJob, job_id)
            if job:
                job.status, job.result, job.error, job.finished_at = status, result, error, utcnow()
                db.commit()
        ACTIVE.discard(job_id)


def latest(db: DBSession, project_id: str, kind: str) -> models.AiJob | None:
    job = (db.query(models.AiJob).filter(models.AiJob.project_id == project_id, models.AiJob.kind == kind)
           .order_by(models.AiJob.created_at.desc()).first())
    return settle(db, job)


def running_in(db: DBSession, project_ids: list[str], recent: timedelta = timedelta(minutes=30)) -> list[models.AiJob]:
    """Jobs to show in Mirmera's activity: running ones, and the ones that ended lately."""
    since = utcnow() - recent
    jobs = (db.query(models.AiJob).filter(models.AiJob.project_id.in_(project_ids))
            .filter((models.AiJob.status == "running") | (models.AiJob.finished_at >= since))
            .order_by(models.AiJob.created_at.desc()).limit(20).all())
    return [settle(db, j) for j in jobs]
