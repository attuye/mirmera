from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
