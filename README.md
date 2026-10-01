# Exploratory Testing Tool

A context-driven, exploratory testing tool: session-based test management
(charters, time-boxed sessions, tagged notes) combined with AI-assisted
charter/risk suggestions and an evidence-trail bug logger that drafts a
debrief report when a session ends.

## Stack

- **Backend**: FastAPI + SQLAlchemy, SQLite by default (swap to Postgres via
  `DATABASE_URL`). AI calls go through a swappable provider interface
  (`app/ai/base.py`) — Claude is implemented, OpenAI and a local model are
  stubbed with the same contract.
- **Frontend**: Vite + React + TypeScript, styled to match the UX mockups
  (dark, developer-tool aesthetic; Space Grotesk + IBM Plex Mono).

## Running it

### Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then set ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```

API docs at http://localhost:8000/docs once it's running.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

App at http://localhost:5173. On first load it creates a demo product
("AddisVerify KYC Platform") if none exists yet.

## What's here vs. what's next

This is the Phase 1 scaffold from the scoping discussion:

- ✅ Product → Artifact → Charter → Session → Note → Bug → Debrief data model
- ✅ Swappable AI provider interface, Claude implemented end-to-end
  (charter suggestion, debrief drafting, artifact summarization)
- ✅ Evidence-trail bug logging (preceding session notes auto-snapshotted
  onto each bug)
- ✅ A runnable frontend covering the four core screens

Not yet built (Phase 2/3 from the scoping doc):
- OpenAI / local-model providers (stubbed in `app/ai/`, same interface)
- Git/Jira/GitHub Issues integration for artifact ingestion (currently paste-only)
- Playwright fixture / CLI to auto-log evidence into a running session
- Multi-user auth (the `org_id` field is there, but nothing enforces it yet)
- Time-breakdown bucketing for the debrief (the field exists, nothing fills it in)
