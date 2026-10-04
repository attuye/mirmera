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

App at http://localhost:5173. It opens on the Projects page: create a project
for the app you are testing (name, description, app URL). Everything else
(risks, charters, sessions, bugs, debriefs) lives inside a project, under
`/projects/<id>/...`, and the project's Settings page holds its environments,
default tester, testing mission, AI provider and tracker targets.

Upgrading an existing `mirmera.db`: on start the backend renames the old
`products` table to `projects` (and `product_id` columns to `project_id`),
keeping all data. Your old demo product shows up as a project.

### Regression tests from a bug

The recorded browser also records your actions: clicks, typing, selects,
checkboxes, Enter and uploads, each with a Playwright locator (test id, label,
placeholder, role and name, text, then CSS). Passwords, one-time codes and
card-like fields are never recorded; the test reads them from environment
variables such as `MIRMERA_SECRET_PASSWORD`.

On a reviewed bug, **Generate regression test** asks the AI for a plan: which
recorded actions lead to the bug, and what should happen instead (assertions
from your oracle). Mirmera renders the Playwright Test code from that plan
itself, so the code is always valid and uses only recorded or checked
locators. Assumptions the AI made are listed for you to check.

With **Mark as a known bug** the test starts with `test.fail()`: CI stays
green while the bug exists and fails the day it is fixed, which is your cue
to remove that line.

**Replay test** runs the plan headless against the app (or another base URL,
such as staging) and says whether it **reproduces the bug**, **did not
reproduce**, is **broken** (a step could not run) or **incomplete** (a secret
or file was missing). Set the `MIRMERA_SECRET_*` variables in the backend's
environment to replay steps that need them. Download the `.spec.ts`, review
it, and add it to your test suite.

### Recording from your own Chrome (extension)

The `extension` folder is a Chrome extension that records a session from your
everyday browser, with your usual logins, instead of the recorded browser.
Load it once: `chrome://extensions`, turn on **Developer mode**, **Load
unpacked**, pick the `extension` folder. Then, with a session running in
Mirmera, open the app you are testing, click the extension, pick the session
and **Record this tab**.

It records that one tab only: your actions (same recorder as the recorded
browser, so regression tests and the reproduction agent can use them),
console and page errors, failed requests, pages visited, screenshots, and
notes and bugs typed in its popup. Passwords are never recorded, and values
in URL query strings are removed (`?token=…`); the backend applies both rules
again. The session screen shows **Recording from Chrome** while it runs. See
`extension/README.md`.

### Reproduction agent

On any bug, **Reproduce with the agent** lets an AI try to reproduce it in a
headless browser, one step at a time: it sees the bug report, your recorded
actions as hints, and the page's accessibility tree, and picks one action per
step (click, fill, select, press, open a page, wait). Guard rails, in code:

- it stays inside the app's address: other sites are refused, and a click
  that leaves the app is undone;
- it never sees secrets: it names a `MIRMERA_SECRET_*` variable and the
  backend types the value;
- **its claim "reproduced" is checked by Mirmera** (the failing request really
  happened, the text really is on the page); an unchecked claim is refused;
- it has a step budget (default 20) and can be cancelled.

When it reproduces the bug, the steps that worked are rerun in fresh browsers
(default 3) to measure how reliably: "2 of 3" means intermittent. **Make a
regression test from these steps** turns them into a Playwright test that
checks the symptom is gone. Useful for bugs logged without the recorded
browser, and for flaky ones.

### Filing bugs to GitHub and Jira

Reviewed bugs and questions can be filed from the Bugs page. Put the
credentials in `backend/.env` (see `.env.example`: a GitHub fine-grained
token with Issues read/write, and/or your Jira Cloud site, email and API
token), restart the backend, then set the project's GitHub repo or Jira
project key in the filing panel or the project's Settings page.

Before filing, **Check for duplicates** searches the tracker's open and closed
issues for words from the bug title and has the AI rate each match (likely
duplicate, related, different; a closed likely duplicate may be a
regression). You then file a new issue, or add the bug as a new occurrence
(a comment) to an existing one. The issue is built from your reviewed report,
the oracle, and what the recorded browser captured. Jira gets the screenshot
attached; GitHub's API cannot take attachments, so there the issue says the
screenshot is kept in Mirmera. Each bug is filed at most once per tracker.

Set `MOCK_TRACKERS=true` to try the flow without accounts (in-memory trackers,
reset when the backend restarts).

### Retest when an issue closes

Trackers can't reach your machine, so Mirmera asks them. **Check closed issues**
on the Regression page looks up every issue filed from the project. When one
was closed since the last check, Mirmera replays the bug's latest regression
test and comments on the issue: **fix verified**, **still fails** (what failed
and the steps replayed), or **couldn't check** (and why). Reopened and closed
again means retested again. Set `RETEST_POLL_MINUTES=30` in `backend/.env` to
check on a timer while the backend runs. With `MOCK_TRACKERS=true`, close a mock
issue with `POST /mock-trackers/issue-state`
(`{"tracker": "github", "target": "owner/repo", "issue_key": "#3"}`).

### Test report

The **Report** page tells the project's testing story for a period (or all time):

1. **The product:** bugs and questions with severity, issue (and whether it's closed), regression test and retest
   results; accepted risks with how many sessions tested them and the bugs found; the last full regression run.
2. **The testing:** each session (charter, tester, date, time, whether the charter was fulfilled), time per
   tester, where the time went, and what each session covered.
3. **How good the testing was:** high risks nobody tested, unfinished charters, sessions without a debrief,
   unreviewed reports, bugs without a regression test, obstacles, open questions, follow-up charters not run
   yet, and how the testers felt.

Everything comes from what was recorded; the AI writes none of it. **Download Markdown** for a ticket or
wiki, or open the **Printable page** and print it to PDF.

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

- ✅ Project → Artifact → Charter → Session → Note → Bug → Debrief data model
- ✅ Projects: a Projects home, project-scoped pages, and per-project settings (app URL, environments, default tester)
- ✅ Swappable AI provider interface, Claude implemented end-to-end
  (charter suggestion, debrief drafting, artifact summarization)
- ✅ Evidence-trail bug logging (preceding session notes auto-snapshotted
  onto each bug)
- ✅ A runnable frontend covering the four core screens
- ✅ Recorded browser: signals, screenshots on bugs, trace and HAR per session
- ✅ Feedback loop: sessions suggest risk re-scores with evidence; the tester
  decides, and each risk keeps its score history
- ✅ Filing to GitHub Issues and Jira Cloud, with an AI-rated duplicate check
- ✅ Regression tests: recorded actions -> AI plan -> rendered Playwright test, verified by replay
- ✅ Reproduction agent: AI drives a browser to reproduce a bug; Mirmera verifies and measures it
- ✅ Chrome extension: record a session from your everyday browser

Not yet built (Phase 2/3 from the scoping doc):
- Importing specs, diffs and past bugs from Git, GitHub and Jira as artifacts (currently paste-only)
- Multi-user auth (the `org_id` field is there, but nothing enforces it yet)
