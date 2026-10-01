# Mirmera

*Mirmera* (ምርመራ, Amharic for "investigation") is AddisVerify's context-driven,
exploratory testing tool: session-based test management
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
python -m playwright install chromium   # browser for the recorded browser, first time only
cp .env.example .env   # then set ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```

API docs at http://localhost:8000/docs once it's running.

### Recorded browser

On the session screen, enter the address of the app you are testing and click
**Open browser**. Mirmera opens a Chromium window; test in it as usual. While
you do, it records console errors, uncaught page errors, failed requests
(4xx/5xx or no response) and the pages you visit, and shows them beside your
notes. **Screenshot** adds the current page to the timeline, and every bug you
log while the browser runs gets a screenshot automatically. On the Bugs page a
bug shows that screenshot and what the page reported in the two minutes before.

When you stop the browser or end the session, a Playwright trace and a HAR
network log are saved; download them from the debrief and open the trace at
https://trace.playwright.dev to replay the session step by step. Signals are
evidence, not verdicts: a 404 can be the right answer, and you decide.

Files go to `backend/evidence/<session id>/` (set `EVIDENCE_DIR` to change it).
They contain whatever the tested app showed, so they are git-ignored. Set
`RUNNER_HEADLESS=true` to run without a window (CI), or `RUNNER_CHROMIUM_PATH`
to use a specific Chromium.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

App at http://localhost:5173. On first load it creates a demo product
("AddisVerify KYC Platform") if none exists yet.

## Tests

CI runs all three on every pull request.

### Backend (pytest)

```bash
cd backend
pip install -r requirements-dev.txt
python -m playwright install chromium   # first time only, for the recorded-browser tests
python -m pytest
```

Uses a throwaway database and the mock AI provider; no API key needed. The
recorded-browser tests run a headless Chromium against a small local site;
without Chromium installed they are skipped locally (and fail in CI).

### Frontend (type check and build)

```bash
cd frontend
npm run build
```

### Browser tests (Playwright)

```bash
cd e2e
npm install
npx playwright install chromium   # first time only
npx playwright test               # or: npm run test:headed to watch it
```

The tests start their own backend (port 8100, fresh database, mock AI) and
frontend (port 5180), so they never touch your real `mirmera.db` and can run
while your dev servers are up. The backend is started with `backend/.venv` if
it exists, otherwise `python`; set `PYTHON` to use another interpreter.

Every test fails on an uncaught page error or an unexpected failed API call,
even if it isn't looking for one. After a failure, `npx playwright show-report`
opens the report with screenshots and a step-by-step trace.

## What's here vs. what's next

This is the Phase 1 scaffold from the scoping discussion:

- ✅ Product → Artifact → Charter → Session → Note → Bug → Debrief data model
- ✅ Swappable AI provider interface, Claude implemented end-to-end
  (charter suggestion, debrief drafting, artifact summarization)
- ✅ Evidence-trail bug logging (preceding session notes auto-snapshotted
  onto each bug)
- ✅ A runnable frontend covering the four core screens
- ✅ Recorded browser: signals, screenshots on bugs, trace and HAR per session
- ✅ Feedback loop: sessions suggest risk re-scores with evidence; the tester
  decides, and each risk keeps its score history

Not yet built (Phase 2/3 from the scoping doc):
- Git/Jira/GitHub Issues integration for artifact ingestion (currently paste-only)
- Multi-user auth (the `org_id` field is there, but nothing enforces it yet)
