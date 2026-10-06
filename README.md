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

`backend/.env`, the SQLite database (`backend/mirmera.db`), the evidence folder and `secret.key` are always found in
`backend/`, even if you start the server from another folder (for example `uvicorn app.main:app --app-dir backend` from
the repo root). Relative paths in `DATABASE_URL`, `EVIDENCE_DIR` and `SECRET_KEY_FILE` are taken inside `backend/`.

API docs at http://localhost:8000/docs once it's running.

### Accounts and workspaces

Open the app and **create an account**: you get a **workspace** (your team or company) and are its owner. The first
account on a database also takes over every project made before accounts existed. Anyone can create an account with
their own empty workspace; set `ALLOW_SIGNUPS=false` to stop that (the very first account is always allowed).

Everything in a project (risks, charters, sessions, bugs, evidence, stories, sprints, reports) is visible only to
members of its workspace; an id from another workspace answers 404. Roles in a workspace:

| Role | Can |
|---|---|
| **viewer** | look at everything |
| **tester** | test: charters, sessions, notes, bugs, risks, stories, sprints, filing, regression runs |
| **admin** | also create projects, change project settings, AI routing and notifications, archive and delete |
| **owner** | the same as admin (it's whoever created the workspace) |

**Account** (bottom of the sidebar) has your name, password, workspaces (add one per client or team, switch, rename)
and tokens for the Chrome extension. Passwords are hashed with scrypt; sign-in uses an httpOnly cookie, and only a
hash of each sign-in or extension token is stored. Ten wrong passwords for an email or address lock sign-in for 15
minutes. Behind HTTPS, set `COOKIE_SECURE=true`.

**Workspace keys** (Account → a workspace → **Keys**, or the link at the top of a project's Settings; admins only): each
workspace enters its own GitHub token, Jira site, email and API token, Claude and OpenAI keys (and an OpenAI-compatible
address), Telegram bot token and Slack webhook. They are encrypted (Fernet) with the server key before they reach the
database, and never come back out: the page shows only whether each is set and its last four characters. Filing,
imports, retests, AI tasks, model checks and notifications in a project all use its workspace's keys, and tokens are
scrubbed from error messages.

- The server key is `MIRMERA_SECRET_KEY` (any long random phrase), or else a `secret.key` file the backend creates in
  `backend/` when it starts (wherever you start it from); the terminal shows its full path on every start. **Back it up with the database**: without it, saved keys can't be read and must be entered again (the
  page says which). Never commit it; it is in `.gitignore`.
- Until a workspace sets a key, the server's own value in `backend/.env` is used. When other companies use your
  Mirmera, set `SERVER_KEYS_FOR_ALL=false` so each workspace must bring its own (and nobody uses yours).
- Model names, the local model address, GitHub labels and the Jira issue type stay in `backend/.env`, per server.

**Members** (Account → a workspace → **Members**): admins invite by email with a role. The invitation is emailed when
SMTP is set up (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` in `backend/.env`); either way the
admin gets the link to send. The link works for 7 days, once, and only for that email: the invited person signs in, or
creates an account from the link (this works even with `ALLOW_SIGNUPS=false`). Inviting the same email again replaces
the old link. Admins change roles and remove people (their sessions and bugs stay); only an owner can make or change an
owner, and a workspace always keeps at least one owner. Anyone can **Leave**.

**Forgot your password?** on the sign-in page emails a reset link (an hour, once); choosing a new password signs out
every browser. It answers the same whether or not the email has an account. Without SMTP, the link is written to the
backend's log for whoever runs the server to pass on. Set `PUBLIC_APP_URL` so links point at your app's address.

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

### API exploration

In a session, the **API** tab (next to **Browser**) sends requests and keeps each one as evidence of the session.
Start a request three ways:

- **Type it**: method, address (a path like `/api/documents` goes to the project's app URL), headers, body.
- **Paste curl**: from DevTools (Network → Copy as cURL), docs or a teammate; bash and Windows `^` line breaks work.
- **From the browser**: requests the recorded browser (or the Chrome extension) saw fail or go slow in this session,
  with their body when it was text.
- **From OpenAPI**: import the API's OpenAPI 3 or Swagger 2.0 file once per project (its address, or paste the JSON or
  YAML), then pick an endpoint. Each comes with a ready request: required query and header parameters, an example JSON
  body built from the schema (its examples first), and a note when it needs auth or a path value such as `{id}`.
  **Send to** picks the spec's server or the project's app URL. The list shows how often this project's sessions tried
  each endpoint and its last status ("2 of 14 endpoints tried"), so untried ones stand out. Importing the same API again
  replaces it; **Refresh from its address** fetches the latest. The spec is also a project artifact, so **Charters →
  Imported before** can suggest charters from its endpoint list.

**Session headers** (a token, say) go with every request in the session and stay in that browser tab only. The answer
shows status, time, size, body (JSON indented) and headers; redirects are shown, not followed. Then vary it (**Send
again**, **Without auth**, **Empty body**), **Note it**, or **Log as bug**, which attaches the request to the bug.
Requests also appear in the session's debrief and AI drafts.

What is kept is redacted: `Authorization`, cookies, API-key and token headers, and password/token fields in JSON or form
bodies and query strings become `•••`. The full answer is shown; its first 20 KB are kept. Requests are sent from the
Mirmera server, so `localhost` and internal staging work; on a server other companies use, set `API_BLOCK_PRIVATE=true`
to refuse private and cloud-metadata addresses. `API_TIMEOUT_SECONDS` (default 30) limits each request.

### Slow requests and accessibility

The recorded browser also records, as evidence (never verdicts):

- **Slow requests:** API calls (fetch/XHR) slower than `SLOW_REQUEST_MS` (2 s) and pages slower than
  `SLOW_PAGE_MS` (3 s) to load, grouped per address.
- **Accessibility findings,** checked when each page loads: images without alt text, form fields without a
  label, buttons and links without a name, low-contrast text (WCAG AA), no page language, no title, duplicate
  ids. A small checker of Mirmera's own (`backend/app/a11y_check.js`), at most five findings per rule per page.
  Turn it off with `RUNNER_ACCESSIBILITY_CHECKS=false`.

They show in the browser panel (accessibility folded under its own heading), reach the AI drafts like other
signals, appear on the coverage map, and bring test ideas: investigate a slow call, and check accessibility by
hand (keyboard only, 200% zoom, a screen reader). Only full page loads are checked; the Chrome extension doesn't
record these yet.

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

### Importing specs, diffs and past bugs

On the Risks page, **Add an artifact** has two tabs. **Paste** takes text, or **Load from file** (.md, .txt,
.diff, .patch). **Import** brings in:

- a **GitHub pull request** (`42`, `owner/repo#42` or its address): title, description and diff, as a Diff;
- **GitHub issues** with a label (default `bug`), open and closed, as Bug history;
- **Jira issues** from a JQL query (default: the project's Bugs, newest first), as Bug history;
- a **git diff** `base...branch` from a repository on the computer running the backend, for work not pushed yet.

Imports use the same `GITHUB_TOKEN` and Jira settings as filing (read access is enough), and the project's repo
and Jira key from Settings. Very large diffs are cut at 60,000 characters, and the artifact says so.

### Heuristics library

**Plan → Heuristics** collects the heuristics Mirmera uses, with a one-line summary, questions to ask and a KYC
example for each: **oracles** (FEW HICCUPPS: how you recognise a problem), **product elements** (SFDIPOT: what
to cover), **quality criteria** (which kinds of good matter) and **techniques** (Goldilocks, interruptions,
follow the data, personas, starve it…). Search them, filter by group, and **add one to a charter**: the charter
shows it ("Try Goldilocks"), and its sessions bring it back as a test idea until a note mentions it. Test ideas,
charter oracles and the bug review's oracle picker link to the library. FEW HICCUPPS, SFDIPOT and the quality
criteria come from James Bach and Michael Bolton's Rapid Software Testing; the summaries are Mirmera's own.

### Charters from a change

The **Charters** page lists the project's charters (click one to start or resume a session) and has **Suggest from
a change**: paste a diff or spec excerpt (or load a file), **Import** a pull request or local git diff, or pick
something **Imported before**, and the AI suggests charters for what it changes. Accepted charters remember the
change they came from ("from PR #42"), shown on the Charters page, the Dashboard and in the report. Pasted text is
saved with a readable name ("Pasted: diff of kyc/selfie.py"). The slower path from the whole product's risks stays
on the Risks page.

### User stories

Most teams plan in stories, so Mirmera keeps them, but the way Rapid Software Testing sees them: a story is a claim
about a change, one oracle among several, not the thing being tested. Open **Plan → Stories** and import one from Jira
(`KYC-12`) or GitHub (`#45`), which reads its title, whole description and status, or write one by hand.

A story **links to** risks and charters; it doesn't own them. A risk stays in the project after its story closes, and one
risk can belong to several stories. On a story's page:

- **What it claims**: the description and acceptance criteria.
- **What's left**: no risks linked, risks no charter here investigates, charters not explored yet, sessions without a
  debrief or with a charter not fulfilled, bug reports to review, high bugs not filed. Each has a button to act on it.
  It never says "tested"; when nothing obvious is left, deciding whether that's enough stays with you.
- **Risks**: link a project risk, or add a new one for the story (it becomes a project risk too).
- **Charters**: link existing ones, or **Suggest charters**: the AI suggests charters from the story's text, and the
  ones you accept are linked to it.
- The **sessions**, **bugs** and **pages explored** under those charters, with a pointer to the coverage map for the
  pages around them, since a change often breaks something next to it.

Stories from a tracker have **Refresh from Jira/GitHub**; stories written here have **Mark done**. Charters and risks
show their stories as links everywhere they appear. Deleting a story keeps its risks, charters and sessions.

### Test strategy

**Plan → Strategy** holds the project's test strategy, or a sprint's (**For**: the whole project or a sprint), in the
sections of the Heuristic Test Strategy Model: mission, context, what we'll test, quality criteria, risks driving the
testing, approach, coverage and gaps, what we won't test and why, logistics. A strategy says what you'll test, how and
why, and what you won't; it is revised as you learn, not a test plan.

- **Draft from the project** fills each section from what Mirmera knows: the testing mission and sprint goal, people and
  environments, stories and the product model, OpenAPI endpoints tried, risks by quality criterion (the sprint's own risks
  first, explored or not), heuristics and oracles in charters, tools used, coverage gaps, charters and testing time.
  **What Mirmera knows** under each section lists those facts.
- **Write it as prose with AI** (optional) drafts every section from the same facts; like the report summary it may only
  use numbers that appear in them (one retry, then refused). It's its own task in **Settings → AI** ("Write the test
  strategy").
- **What we won't test** is never decided for you: Mirmera lists candidates (retired risks, low-scoring ones, deprecated
  endpoints) as questions for the team.
- **Save as version N** keeps every version per focus; open an older one from **Version**. **Markdown** and **Print**
  export a saved version. A sprint's page links to its strategy; deleting a sprint deletes its strategy versions.

### Sprints

A sprint is a name, its dates (the end day included), an optional goal and the stories planned for it. Open
**Plan → Sprints**: the new-sprint form suggests the next name and dates after the last sprint. A story is in one sprint
at a time; adding it to the next sprint moves it there (carried over). Pick a story's sprint on its page too, and filter
the Stories page by sprint.

A sprint's page answers "what did we test this sprint?" with the whole answer, not just the plan:

- **What's left** in its stories (the same items as each story's page), stories not done once the sprint has ended, and
  **high risks not explored in these dates**, whether a story here names them or not.
- **Testing in these dates**: sessions, time and bugs **under the sprint's stories** and **outside them**, with the
  charters tested outside the plan listed, since that is often where the surprises are.
- **Report** and **Coverage** open those pages for the sprint's dates; both also have a **Sprint** picker next to their
  periods. **Trends** stays weekly, with each week's sprint in the testing-time tooltip and the table.

Deleting a sprint keeps its stories, outside any sprint.

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

### Exploration agent

On a story's charter, **Let Mirmera explore** sends an AI through the charter in a headless
browser on the project's app URL. It works the way a tester runs a session:
- it follows the mission and test ideas, and varies the data;
- it writes notes on what it tried;
- each time something looks wrong, it reports a problem.

Mirmera checks each problem's symptom itself before believing it: the failed request
really happened, or the text really is on the page. Each checked problem becomes a **bug
draft** with the steps, expected, actual and a screenshot. A tester reviews it like any
other bug; nothing goes to a tracker on its own.

The agent's work is an ordinary session by "Mirmera agent", left paused. Review its notes,
then **End Session** to draft the debrief.

The guard rails are the reproduction agent's:
- it stays inside the app's address;
- it only types passwords through `MIRMERA_SECRET_*` names, and never sees the values;
- it has a step budget, a 15-minute limit and a **Stop** button.

It uses the project's AI setting for "Reproduction and exploration agents".

From Jira, planning a work item (the panel button, or assigning it to Mirmera) also
explores its first charter when the project has an app URL. Jira gets the result: what
was covered, and the bug drafts.

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

### Mirmera in Jira

`jira-app/` is an Atlassian Forge app. It puts a **Mirmera** panel on every Jira issue:

- for a story, the risks, charters and sessions behind it, and what hasn't been looked at yet;
- for a bug Mirmera filed, where it was found and the latest retest.

A workspace admin links a Jira site under **Account → workspace → Jira**, with a one-time
code that a Jira admin pastes into the app. Calls from the app carry a token signed by
Atlassian, and Mirmera checks it against `JIRA_APP_ID`. Setup steps (Forge CLI, a tunnel,
deploy, install) are in [`jira-app/README.md`](jira-app/README.md).

On a work item Mirmera knows nothing about, the panel offers **Plan testing with Mirmera**.
It turns the item into a story with proposed risks and charters, with no Rovo needed.
The app also adds **Mirmera as a Rovo agent**. Assign a work item to it, and Mirmera turns
the item into a story with proposed risks and charters for a tester to accept. Mention it in
a comment to hear what's left to test. Its work is listed on the workspace's Jira page.

### Test-idea nudges

While a session runs, **Test ideas** under the note box suggests what to try next, from what this session has
done (no AI, so it's instant and works offline). Each idea says why it appeared and names the heuristic behind it:
failures the browser recorded that no bug or note covers yet (shown first), the charter's risks and test ideas no
note mentions, fields filled only with ordinary values (boundary and odd inputs chosen by field type: dates,
numbers, emails, phones, text; never for secret fields), choices made one way, uploads, submit buttons
(double-click, Back, refresh, timeout), pages other sessions opened but never tried, and halfway through with
nothing found. Click a suggestion to start a note with it; **Tried it** logs a test note; **Not now** hides the
idea for the rest of the session.

### Coverage map

The **Coverage** page shows where the testing actually went. Every page the recorded browser (or the Chrome
extension) opened is listed by path, with ids collapsed (`/orders/42` and `/orders/43` are `/orders/:id`; staging
and production count as one page): how many sessions went there, the actions and distinct controls used (never
the values typed), the failed requests and errors recorded there, and the bugs logged while on it. Pages are
flagged **Not tried** (opened, nothing clicked), **Problems, no bug**, **Stale** (not visited for 14 days) or
**One tester**. **Product areas** shows each element of the product model, explored when a session's charter
targets a risk naming it. **Where to look next** lists the gaps.

### Trends

**Insights → Trends** shows the direction, week by week (Monday to Sunday, the last 8, 12 or 26 weeks): testing
time, bugs found by severity, bugs per hour of testing, where the time went, fixes checked after an issue closed,
and risk exposure (the sum of the accepted risks' scores at the end of each week, rebuilt from their re-scores).
**What changed** compares the last four weeks with the four before ("Bugs per hour of testing: 0.87 → 0.37"),
with a note on what a change can mean, never a verdict. Every chart has a hover tooltip, and **Show as table**
gives the numbers.

### Test report

The **Report** page tells the project's testing story for a period, in two views:

- **Summary** (default, one screen): the totals, **At a glance** (high bugs still open, high risks not tested, fixes
  that didn't hold or were verified, unfinished charters, reports to review, each with a link to act on it), three
  small charts (bugs by severity, risks tested, where the time went) and the top five bugs.
- **Full**: the three parts of the story, folded, with their counts in the headers. *How good the testing was* opens by
  default with its **Gaps**, each linked to where to act (make a charter, write the debrief, review, generate tests).
  Long lists show five rows first; long debrief text shows its start.

Everything comes from what was recorded. **Markdown** and **Print** export the view you are on, so the Summary makes
a short report for a ticket. Set `PUBLIC_APP_URL` so the exported links open Mirmera.

**Draft summary with AI** (optional) writes two to four sentences from the report's own lines only (totals, at a
glance, charts, gaps, top bugs) and shows which lines it used. A summary with a number that isn't in the report is
refused (one retry, then a clear message). You keep, edit or discard it; a kept summary is saved for that period,
shown labelled ("Drafted by AI (provider · model), edited by you") and included in the exports. It is its own task
in **Settings → AI** ("Summarise the test report"), so it can use a different model from the rest.

### Notifications

Mirmera can send a short message to **Telegram** or **Slack** when something needs you, so you don't have to keep
the app open. Set it up per project in **Settings → Notifications**: a Telegram chat id and/or "Send to Slack", and
which events to hear about:

- **A closed issue still fails its retest** (on by default), and optionally **a closed issue's fix is verified**
- **A regression run has failures, broken tests or known bugs that now pass** (on by default)
- **A bug is reviewed as high severity** (on by default)

**Send a test message** checks the setup; **Recent** shows what was sent, and why a message failed. Only the
secrets go in `backend/.env`, never in the app:

- Telegram: create a bot with @BotFather and set `TELEGRAM_BOT_TOKEN`. Send the bot a message (or add it to a
  group) before using its chat id; bots can't start a chat.
- Slack: create an incoming webhook for the channel and set `SLACK_WEBHOOK_URL`.

Set `PUBLIC_APP_URL` so messages link to the page to act on. `MOCK_NOTIFICATIONS=true` keeps messages in the app
instead of sending them. Tokens and webhook addresses are removed from any error shown or logged.

## Tests

CI runs all four on every pull request.

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

### Jira app (manifest and bundles)

```bash
cd jira-app
npm install
npm run check
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
- ✅ Projects: everything belongs to a project with its app address, environments and default tester
- ✅ Steps to the bug: recorded per bug, editable, and replayed exactly by the regression test
- ✅ Isolate the bug: replay variations of the steps to find what the bug depends on
- ✅ AI provider per task, with a model check (e.g. local qwen for debriefs, Claude for bug reports)
- ✅ Regression page: run every test, keep a history, export them as a Playwright project
- ✅ Retest when an issue closes: replay the test and comment the result on the issue
- ✅ Test report (Summary and Full views, optional AI summary), coverage map, test-idea nudges
- ✅ Import specs, diffs and past bugs: GitHub pull requests and issues, Jira issues, local git diffs, files
- ✅ User stories: import from Jira or GitHub, link risks and charters, see the testing under each and what is left
- ✅ Test strategy: HTSM sections drafted from the project (and a sprint), optional AI prose, versions, exports
- ✅ Sprints: dates and stories, everything tested in those dates (inside and outside the plan), report and coverage per sprint
- ✅ Accounts and workspaces: sign up and in, every route checked against the workspace and role, extension tokens
- ✅ Members: invitations by email or link, roles, removing and leaving, password reset
- ✅ API exploration in sessions: typed, curl, replayed or OpenAPI requests, kept as redacted evidence, logged as bugs
- ✅ Workspace keys: each workspace's GitHub, Jira, AI, Telegram and Slack keys, encrypted, never shown back
- ✅ Notifications: Telegram and Slack messages for fixes that didn't hold, regression problems and high bugs

Not yet built:
