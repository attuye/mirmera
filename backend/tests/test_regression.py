"""Regression tests: actions recorded in the real recorded browser -> AI plan
(mock) -> rendered Playwright code -> replay against a small app with a
planted bug ("Passport" makes POST /api/verify fail), then with the bug fixed.
Also: the renderer, plan resolution and guard rails, without a browser."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import models, schemas
from app.config import settings
from app.database import SessionLocal
from app.regression.plan import resolve
from app.regression.render import render
from app.runner import manager
from tests.test_runner import chromium_available, headless_runner  # noqa: F401  (fixtures)

APP = b"""<!doctype html><title>KYC verify</title>
<form id=f>
  <label for=name>Full name</label><input id=name>
  <label>Password <input type=password name=password></label>
  <select aria-label="Document type"><option>National ID</option><option>Passport</option></select>
  <label for=expiry>Expiry date</label><input id=expiry type=date>
  <input type=checkbox id=terms><label for=terms>I accept the terms</label>
  <button type=submit>Verify</button>
</form>
<p role=status id=msg></p>
<a href="http://127.0.0.1:9/">Elsewhere</a>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault()
  const doc = document.querySelector('select').value
  const r = await fetch('/api/verify', { method: 'POST', body: doc })
  document.getElementById('msg').textContent = r.ok ? 'Verified' : 'Something went wrong'
})
</script>"""


class App(BaseHTTPRequestHandler):
    fixed = False
    flaky = False  # Passport fails on every other request only: an intermittent bug
    posts = 0

    def do_GET(self):
        self._send(200 if self.path == "/" else 404, APP if self.path == "/" else b"", "text/html")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        App.posts += 1
        broken = body == b"Passport" and not App.fixed and (not App.flaky or App.posts % 2 == 1)
        self._send(500 if broken else 200, b'{"ok": %s}' % (b"false" if broken else b"true"), "application/json")

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def app_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(autouse=True)
def bug_present():
    App.fixed, App.flaky, App.posts = False, False, 0
    yield
    App.fixed, App.flaky, App.posts = False, False, 0


def wait_for(client, session, condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        counts = client.get(f"/sessions/{session['id']}/runner").json()["counts"]
        if condition(counts):
            return counts
        if time.monotonic() > deadline:
            raise AssertionError(f"Timed out; counts were {counts}")
        time.sleep(0.2)


@pytest.fixture
def recorded_bug(client, session, app_url, chromium_available):  # noqa: F811
    """The tester reproduces the bug in the recorded browser, then logs and reviews it."""
    assert client.post(f"/sessions/{session['id']}/runner", json={"url": app_url + "/"}).status_code == 200
    page = manager.get(session["id"])._current_page()

    async def act():
        await page.get_by_label("Full name").fill("Abebe Kebede")
        await page.keyboard.press("Tab")
        await page.get_by_label("Password").fill("s3cret-pass")
        await page.keyboard.press("Tab")
        await page.get_by_label("Document type").focus()
        await page.keyboard.press("ArrowDown")  # a real choice (trusted event), like a user
        await page.get_by_label("Expiry date").focus()  # typing starts at the first segment
        await page.keyboard.type("01012030")  # typed digit by digit, like a user
        await page.keyboard.press("Tab")
        await page.get_by_text("I accept the terms").click()
        await page.get_by_role("button", name="Verify").click()
        await page.get_by_text("Something went wrong").wait_for()

    manager.loop.run(act(), timeout=30)
    wait_for(client, session, lambda c: c.get("failed_request") and c.get("action", 0) >= 6)
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Passport verification fails"}).json()
    client.patch(f"/bugs/{bug['id']}", json={
        "kind": "bug", "steps_to_reproduce": ["Fill the form", "Choose Passport", "Click Verify"],
        "expected": "Verified", "actual": "Something went wrong",
        "oracle_heuristic": "product", "oracle_explanation": "National ID verifies; Passport should too",
    })
    return bug


def generate(client, bug, **body):
    r = client.post(f"/bugs/{bug['id']}/regression-test", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def replay(client, test, **body):
    r = client.post(f"/regression-tests/{test['id']}/replay", json=body)
    assert r.status_code == 200, r.text
    return r.json()["replay"]


# ---------- recording ----------

def test_the_testers_actions_are_recorded_with_locators_and_no_secrets(client, session, recorded_bug):
    actions = client.get(f"/sessions/{session['id']}/evidence?kind=action").json()
    assert [a["summary"] for a in actions] == [
        'fill label "Full name" = "Abebe Kebede"',
        'fill label "Password" = (hidden: MIRMERA_SECRET_PASSWORD)',
        'select "Passport" in label "Document type"',
        'fill label "Expiry date" = "2030-01-01"',  # once, not once per digit typed
        'check label "I accept the terms"',
        'click button "Verify"',
    ]
    with SessionLocal() as db:
        stored = json.dumps([e.data for e in db.query(models.Evidence).all()])
    assert "s3cret-pass" not in stored


# ---------- generating ----------

def test_the_generated_test_replays_the_actions_and_asserts_the_oracle(client, recorded_bug, app_url):
    test = generate(client, recorded_bug)
    code = test["code"]
    assert test["filename"] == "passport-verification-fails.spec.ts"
    assert f'const BASE_URL = process.env.BASE_URL ?? "{app_url}"' in code
    assert 'await page.goto(BASE_URL + "/")' in code
    assert 'await page.getByLabel("Full name", { exact: true }).fill("Abebe Kebede")' in code
    assert "fill(process.env.MIRMERA_SECRET_PASSWORD ?? '')" in code and "s3cret" not in code
    assert 'await page.getByLabel("Document type", { exact: true }).selectOption("Passport")' in code
    assert 'await page.getByRole("button", { name: "Verify", exact: true }).click()' in code
    assert "test.fail(true, " in code  # known failing until the fix
    assert 'failedRequests.filter((r) => r.includes("/api/verify"))' in code
    assert "// Oracle: Product (consistency within the product). National ID verifies; Passport should too" in code
    assert (test["ai_provider"], test["prompt_version"]) == ("mock", "regression-test-v1")


def test_not_known_failing_leaves_out_test_fail(client, recorded_bug):
    assert "test.fail(" not in generate(client, recorded_bug, known_failing=False)["code"]


# ---------- replaying ----------

def test_replay_reproduces_the_bug_then_passes_once_fixed(client, recorded_bug, monkeypatch):
    test = generate(client, recorded_bug)

    first = replay(client, test)  # the password is not known to the backend
    assert first["outcome"] == "incomplete"
    assert first["steps"][2]["status"] == "skipped" and "MIRMERA_SECRET_PASSWORD" in first["steps"][2]["detail"]

    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    result = replay(client, test)
    assert result["outcome"] == "reproduces"
    assert all(s["status"] == "ok" for s in result["steps"])
    assert "/api/verify -> 500" in result["assertions"][0]["detail"]

    App.fixed = True
    assert replay(client, test)["outcome"] == "passes"
    latest = client.get(f"/bugs/{recorded_bug['id']}/regression-test").json()
    assert latest["replay"]["outcome"] == "passes" and latest["replayed_at"]


def test_replay_against_another_base_url_reports_a_broken_step(client, recorded_bug):
    result = replay(client, generate(client, recorded_bug), base_url="http://127.0.0.1:9")
    assert result["outcome"] == "broken"
    assert result["steps"][0]["status"] == "failed" and result["base_url"] == "http://127.0.0.1:9"
    assert result["steps"][0]["description"] == "open http://127.0.0.1:9/"  # where it really went
    assert {a["status"] for a in result["assertions"]} == {"not_run"}


def test_download_is_the_spec_file(client, recorded_bug):
    test = generate(client, recorded_bug)
    r = client.get(f"/regression-tests/{test['id']}/download")
    assert r.headers["content-disposition"] == 'attachment; filename="passport-verification-fails.spec.ts"'
    assert r.text == test["code"]


# ---------- guard rails (no browser) ----------

def test_only_reviewed_bugs_with_a_recording(client, session):
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Something odd"}).json()
    assert client.post(f"/bugs/{bug['id']}/regression-test", json={}).status_code == 409  # unreviewed
    client.patch(f"/bugs/{bug['id']}", json={"kind": "question"})
    assert client.post(f"/bugs/{bug['id']}/regression-test", json={}).status_code == 409  # no oracle
    client.patch(f"/bugs/{bug['id']}", json={"kind": "bug", "oracle_heuristic": "claims", "oracle_explanation": "Spec"})
    r = client.post(f"/bugs/{bug['id']}/regression-test", json={})
    assert r.status_code == 409 and "Nothing was recorded" in r.json()["detail"]
    assert client.get(f"/bugs/{bug['id']}/regression-test").json() is None


def evidence(summary, data, url="http://app.test/form"):
    return models.Evidence(kind="action", summary=summary, url=url, data=data)


def test_resolve_drops_invented_steps_and_adds_the_first_page():
    actions = [evidence('click button "Go"', {"action": "click", "locator": {"method": "role", "role": "button", "name": "Go"}})]
    plan = schemas.LLMRegressionPlan(
        test_title="Go button",
        steps=[{"kind": "recorded", "action_index": 0}, {"kind": "recorded", "action_index": 7},
               {"kind": "goto", "url": "http://evil.test/"}],
        assertions=[{"kind": "no_console_errors", "why": "Page should not error"}],
    )
    resolved, warnings = resolve(plan, actions, ["http://app.test/form"], "http://app.test")
    assert [s["type"] for s in resolved["steps"]] == ["goto", "action"]
    assert resolved["steps"][0]["url"] == "http://app.test/form"
    assert len(warnings) == 3  # missing action [7], unvisited page, added first page


def test_the_ai_cannot_use_css_or_unknown_roles_in_assertions():
    with pytest.raises(ValueError):
        schemas.PlanAssertion(kind="visible", locator={"method": "css", "value": "#x"}, why="because")
    with pytest.raises(ValueError):
        schemas.PlanLocator(method="role", role="marquee")
    with pytest.raises(ValueError):
        schemas.PlanAssertion(kind="text", locator={"method": "text", "value": "x"}, why="because")  # no expected


def test_recorded_text_cannot_break_out_of_the_code():
    nasty = 'x"); require("child_process").exec("rm -rf /"); //\n*/ `${BASE_URL}`'
    plan = {"title": nasty, "base_url": "http://app.test", "assumptions": ["line one\nline two */"],
            "steps": [{"type": "goto", "url": "http://app.test/", "why": "open\n// injected"},
                      {"type": "action", "why": "", "action": {"action": "fill", "value": nasty,
                                                               "locator": {"method": "label", "value": nasty}}}],
            "assertions": [{"kind": "text", "locator": {"method": "text", "value": nasty}, "expected": nasty, "why": nasty}]}
    code = render(plan, bug_title=nasty, oracle=nasty, issue_links=[], provenance="mock", known_failing=False)
    literal = json.dumps(nasty)
    assert code.count(literal) >= 4  # always inside a JSON string literal
    for line in code.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("require(") and not stripped.startswith("*/")
    assert '// injected' not in code.replace("open // injected", "")  # comments stay on one line


# ---------- the rendered code, run by real Playwright Test ----------

E2E = __import__("pathlib").Path(__file__).resolve().parents[2] / "e2e"


def run_generated(code: str, app_url: str, env: dict) -> int:
    """Runs a generated spec with the e2e project's Playwright Test. Returns the exit code."""
    import os
    import shutil
    import subprocess
    # On Windows npx is npx.cmd, which subprocess only finds by its full path; shutil.which follows PATHEXT.
    npx = shutil.which("npx")
    if not npx:
        if os.environ.get("REQUIRE_GENERATED_RUN"):
            raise AssertionError("npx (Node.js) is not on PATH")
        pytest.skip("Node.js (npx) is not on PATH")
    folder = E2E / ".generated"
    folder.mkdir(exist_ok=True)
    (folder / "regression.spec.ts").write_text(code)
    (folder / "generated.config.ts").write_text(
        "import { defineConfig } from '@playwright/test'\n"
        "export default defineConfig({ testDir: '.', workers: 1, retries: 0, reporter: 'line',\n"
        "  use: { launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {} } })\n")
    proc = subprocess.run([npx, "playwright", "test", "--config", ".generated/generated.config.ts"],
                          cwd=E2E, env={**os.environ, "BASE_URL": app_url, **env},
                          capture_output=True, text=True, timeout=180)
    print(proc.stdout[-2000:], proc.stderr[-2000:])
    if "Executable doesn't exist" in proc.stdout + proc.stderr and not os.environ.get("REQUIRE_GENERATED_RUN"):
        pytest.skip("The e2e project's browser is not installed: cd e2e && npx playwright install chromium")
    return proc.returncode


@pytest.mark.skipif(not (E2E / "node_modules" / "@playwright" / "test").exists()
                    and not __import__("os").environ.get("REQUIRE_GENERATED_RUN"),
                    reason="needs the e2e project's Playwright Test (cd e2e && npm ci)")
def test_the_rendered_code_runs_in_playwright_test(client, recorded_bug, app_url):
    code = generate(client, recorded_bug)["code"]
    secret = {"MIRMERA_SECRET_PASSWORD": "s3cret-pass"}
    # Bug present: the assertion fails, which test.fail() expects, so the run succeeds.
    assert run_generated(code, app_url, secret) == 0
    # Bug fixed: the test now passes, which test.fail() reports as a failure: time to remove the line.
    App.fixed = True
    assert run_generated(code, app_url, secret) == 1


def test_uploads_work_in_es_module_projects_too():
    """__dirname does not exist in ES-module test projects; test.info().file does."""
    plan = {"title": "Upload", "base_url": "http://app.test", "assumptions": [],
            "steps": [{"type": "goto", "url": "http://app.test/", "why": ""},
                      {"type": "action", "why": "", "action": {"action": "upload", "files": ["id.jpg"],
                                                               "locator": {"method": "label", "value": "Photo"}}}],
            "assertions": [{"kind": "no_console_errors", "why": "No errors"}]}
    code = render(plan, bug_title="Upload", oracle="x", issue_links=[], provenance="mock", known_failing=False)
    assert "__dirname" not in code
    assert "path.join(path.dirname(test.info().file), 'fixtures', \"id.jpg\")" in code
