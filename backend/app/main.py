import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.ai.base import ProviderError, ProviderNotConfigured, UnknownProvider
from app.auth import guard
from app.config import settings
from app.database import Base, add_missing_columns, apply_renames, engine
from app.routers import projects, artifacts, bugs, charters, sessions, debriefs, risks, runner, risk_feedback, trackers, regression, reproduce, extension, isolate, suite, retests, report, coverage, nudges, notifications, stories, sprints, ai, members, workspace_keys, jira_app, api_explore, openapi, strategy, explore, agent_activity, ai_jobs, sign_in as sign_in_routes, auth as auth_routes, compare as compare_routes, dismissals as dismissal_routes, test_data as test_data_routes
from app.runner import RunnerConflict, RunnerUnavailable, manager as runner_manager
from app.trackers import TrackerError, TrackerNotConfigured, UnknownTracker

for change in apply_renames():  # moves renamed tables and columns, keeping their data
    logging.getLogger(__name__).warning("Database upgraded: renamed %s", change)
Base.metadata.create_all(bind=engine)  # creates new tables
for column in add_missing_columns():  # adds new columns to existing tables
    logging.getLogger(__name__).warning("Database upgraded: added column %s", column)

def quiet_connection_resets(loop, context):
    """On Windows, asyncio logs a full traceback (WinError 10054) whenever a browser or
    Chromium drops a connection, e.g. a page refreshed while a request was running.
    It is harmless, so it isn't logged; anything else goes to the default handler."""
    if isinstance(context.get("exception"), ConnectionResetError):
        return
    loop.default_exception_handler(context)


@asynccontextmanager
async def lifespan(_: FastAPI):
    import asyncio
    import sys
    if sys.platform == "win32":
        asyncio.get_running_loop().set_exception_handler(quiet_connection_resets)
    from app.keys import ensure_server_key
    ensure_server_key()  # the key that encrypts workspace keys exists from the start, at a known place
    poller = None
    if settings.retest_poll_minutes > 0:  # check closed issues on a timer
        poller = asyncio.create_task(retests.poll_forever(settings.retest_poll_minutes))
    yield
    if poller:
        poller.cancel()
    # Close any test browser still open, so no Chromium outlives the server.
    runner_manager.stop_all()


app = FastAPI(title="Mirmera API", lifespan=lifespan,  # API_DOCS=false hides /docs and /openapi.json on a server
              **({} if settings.api_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}))

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,  # the sign-in cookie
    expose_headers=["X-Frame-Seq", "X-Frame-Url", "X-Frame-Live"],  # the live view of Mirmera's browser reads them
)
GUARDED = [Depends(guard)]  # every route below checks who is calling and their role in the project's workspace

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


app.include_router(auth_routes.router)  # signing in can't require being signed in
app.include_router(members.router)  # checks membership itself; invitation links work signed out
app.include_router(workspace_keys.router)  # admins of the workspace only
app.include_router(jira_app.router)  # workspace admins, or the Jira app with an Atlassian-signed token
app.include_router(extension.router, dependencies=GUARDED)  # before sessions: /sessions/active must not match /sessions/{id}
app.include_router(projects.router, dependencies=GUARDED)
app.include_router(artifacts.router, dependencies=GUARDED)
app.include_router(charters.router, dependencies=GUARDED)
app.include_router(sessions.router, dependencies=GUARDED)
app.include_router(debriefs.router, dependencies=GUARDED)
app.include_router(debriefs.list_router, dependencies=GUARDED)
app.include_router(sessions.bugs_list_router, dependencies=GUARDED)
app.include_router(risks.router, dependencies=GUARDED)
app.include_router(bugs.router, dependencies=GUARDED)
app.include_router(runner.router, dependencies=GUARDED)
app.include_router(risk_feedback.router, dependencies=GUARDED)
app.include_router(trackers.router, dependencies=GUARDED)
app.include_router(regression.router, dependencies=GUARDED)
app.include_router(reproduce.router, dependencies=GUARDED)
app.include_router(explore.router, dependencies=GUARDED)
app.include_router(agent_activity.router, dependencies=GUARDED)  # the workspace route checks membership itself
app.include_router(isolate.router, dependencies=GUARDED)
app.include_router(suite.router, dependencies=GUARDED)
app.include_router(retests.router, dependencies=GUARDED)
app.include_router(report.router, dependencies=GUARDED)
app.include_router(coverage.router, dependencies=GUARDED)
app.include_router(nudges.router, dependencies=GUARDED)
app.include_router(notifications.router, dependencies=GUARDED)
app.include_router(sign_in_routes.router, dependencies=GUARDED)
app.include_router(stories.router, dependencies=GUARDED)
app.include_router(sprints.router, dependencies=GUARDED)
app.include_router(api_explore.router, dependencies=GUARDED)
app.include_router(openapi.router, dependencies=GUARDED)
app.include_router(strategy.router, dependencies=GUARDED)
app.include_router(ai.router, dependencies=GUARDED)
app.include_router(ai_jobs.router, dependencies=GUARDED)
app.include_router(compare_routes.router, dependencies=GUARDED)
app.include_router(dismissal_routes.router, dependencies=GUARDED)
app.include_router(test_data_routes.router, dependencies=GUARDED)


@app.get("/health")
def health():
    return {"status": "ok"}
