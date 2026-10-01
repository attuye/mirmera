from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.ai.base import ProviderError, ProviderNotConfigured, UnknownProvider
from app.database import Base, engine
from app.routers import products, artifacts, charters, sessions, debriefs

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mirmera API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # the Vite dev server
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

app.include_router(products.router)
app.include_router(artifacts.router)
app.include_router(charters.router)
app.include_router(sessions.router)
app.include_router(debriefs.router)
app.include_router(debriefs.list_router)
app.include_router(sessions.bugs_list_router)


@app.get("/health")
def health():
    return {"status": "ok"}
