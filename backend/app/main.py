import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.ai.base import ProviderError, ProviderNotConfigured, UnknownProvider
from app.config import settings
from app.database import Base, add_missing_columns, apply_renames, engine
from app.routers import projects, artifacts, bugs, charters, sessions, debriefs, risks, runner, risk_feedback, trackers, regression, reproduce, extension, isolate, suite, ai
from app.runner import RunnerConflict, RunnerUnavailable, manager as runner_manager
from app.trackers import TrackerError, TrackerNotConfigured, UnknownTracker

for change in apply_renames():  # moves renamed tables and columns, keeping their data
    logging.getLogger(__name__).warning("Database upgraded: renamed %s", change)
Base.metadata.create_all(bind=engine)  # creates new tables
for column in add_missing_columns():  # adds new columns to existing tables
    logging.getLogger(__name__).warning("Database upgraded: added column %s", column)

@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    # Close any test browser still open, so no Chromium outlives the server.
    runner_manager.stop_all()


app = FastAPI(title="Mirmera API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# AI provider problems become clear errors instead of a bare 500.
# The most specific handler wins, so these three cover every ProviderError.
@app.exception_handler(UnknownProvider)
async def unknown_provider(_: Request, err: UnknownProvider):
    return JSONResponse(status_code=400, content={"detail": str(err)})


@app.exception_handler(ProviderNotConfigured)
async def provider_not_configured(_: Request, err: ProviderNotConfigured):
    return JSONResponse(status_code=503, content={"detail": str(err)})


@app.exception_handler(ProviderError)
async def provider_error(_: Request, err: ProviderError):
    return JSONResponse(status_code=502, content={"detail": str(err)})

# Issue tracker problems: the most specific handler wins, as for AI providers.
@app.exception_handler(UnknownTracker)
async def unknown_tracker(_: Request, err: UnknownTracker):
    return JSONResponse(status_code=400, content={"detail": str(err)})


@app.exception_handler(TrackerNotConfigured)
async def tracker_not_configured(_: Request, err: TrackerNotConfigured):
    return JSONResponse(status_code=503, content={"detail": str(err)})


@app.exception_handler(TrackerError)
async def tracker_error(_: Request, err: TrackerError):
    return JSONResponse(status_code=502, content={"detail": str(err)})


@app.exception_handler(RunnerUnavailable)
async def runner_unavailable(_: Request, err: RunnerUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(err)})


@app.exception_handler(RunnerConflict)
async def runner_conflict(_: Request, err: RunnerConflict):
    return JSONResponse(status_code=409, content={"detail": str(err)})


app.include_router(extension.router)  # before sessions: /sessions/active must not match /sessions/{id}
app.include_router(projects.router)
app.include_router(artifacts.router)
app.include_router(charters.router)
app.include_router(sessions.router)
app.include_router(debriefs.router)
app.include_router(debriefs.list_router)
app.include_router(sessions.bugs_list_router)
app.include_router(risks.router)
app.include_router(bugs.router)
app.include_router(runner.router)
app.include_router(risk_feedback.router)
app.include_router(trackers.router)
app.include_router(regression.router)
app.include_router(reproduce.router)
app.include_router(isolate.router)
app.include_router(suite.router)
app.include_router(ai.router)


@app.get("/health")
def health():
    return {"status": "ok"}
