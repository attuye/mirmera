"""Browser session runner: a real headless Chromium against a small local site
that misbehaves on purpose (console errors, a failing API call)."""
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import models
from app.config import settings

HOME = b"""<!doctype html><title>KYC upload</title>
<h1>Upload your ID</h1><a href="/upload">Upload</a>
<script>
  console.error('Upload widget failed to init');
  console.error('Upload widget failed to init');
  console.log('not an error, ignored');
  fetch('/api/documents');
</script>"""
UPLOAD = b"<!doctype html><title>Upload</title><h1>Choose a file</h1>"


class Target(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/documents":
            self._send(500, b'{"error":"boom"}', "application/json")
        elif self.path == "/upload":
            self._send(200, UPLOAD, "text/html")
        elif self.path == "/":
            self._send(200, HOME, "text/html")
        else:  # includes /favicon.ico, which the runner ignores
            self._send(404, b"not found", "text/plain")

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="module")
def chromium_available():
    """Locally, skip with a hint if Chromium was never installed; in CI, fail."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(executable_path=settings.runner_chromium_path or None).close()
    except Exception as err:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Chromium for Playwright is not available ({str(err).splitlines()[0]}). "
                    "Run: python -m playwright install chromium")


@pytest.fixture(autouse=True)
def headless_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "runner_headless", True)
    monkeypatch.setattr(settings, "evidence_dir", str(tmp_path / "evidence"))


@pytest.fixture
def browser(client, session, site, chromium_available):
    """A running browser on the site's home page, with its first signals in."""
    r = client.post(f"/sessions/{session['id']}/runner", json={"url": site})
    assert r.status_code == 200, r.text
    wait_for(client, session, lambda c: c.get("failed_request") and c.get("console_error") == 2)
    return r.json()


def wait_for(client, session, condition, timeout=10.0):
    """Signals arrive asynchronously; poll the status like the UI does."""
    deadline = time.monotonic() + timeout
    while True:
        status = client.get(f"/sessions/{session['id']}/runner").json()
        if condition(status["counts"]):
            return status
        if time.monotonic() > deadline:
            raise AssertionError(f"Timed out; counts were {status['counts']}")
        time.sleep(0.2)


def evidence(client, session, kind=None):
    query = f"?kind={kind}" if kind else ""
    return client.get(f"/sessions/{session['id']}/evidence{query}").json()


# ---------- signals ----------

def test_the_runner_records_errors_failed_requests_and_pages(client, session, site, browser):
    assert browser["running"] and browser["window_open"]
    assert browser["url"] == site + "/" and browser["title"] == "KYC upload"

    errors = evidence(client, session, "console_error")
    assert [(e["summary"], e["count"]) for e in errors] == [("Upload widget failed to init", 2)]  # one row, counted
    failed = evidence(client, session, "failed_request")
    assert [e["summary"] for e in failed] == [f"GET {site}/api/documents → 500"]  # favicon 404 ignored
    assert [e["url"] for e in evidence(client, session, "navigation")] == [site + "/"]


def test_an_unreachable_app_is_reported_not_fatal(client, session, chromium_available):
    r = client.post(f"/sessions/{session['id']}/runner", json={"url": "http://127.0.0.1:9"})
    assert r.status_code == 200
    assert any(e["summary"].startswith("Could not open http://127.0.0.1:9")
               for e in evidence(client, session, "failed_request"))


# ---------- screenshots and bugs ----------

def test_screenshot_is_saved_and_served(client, session, site, browser):
    shot = client.post(f"/sessions/{session['id']}/runner/screenshot", json={"caption": "Empty upload form"}).json()
    assert (shot["kind"], shot["summary"], shot["has_file"]) == ("screenshot", "Empty upload form", True)
    image = client.get(f"/evidence/{shot['id']}/file")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_bug_logged_while_testing_gets_a_screenshot_and_recent_signals(client, session, browser):
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Upload widget never loads"}).json()
    assert [(e["kind"], e["summary"]) for e in bug["evidence"]] == [
        ("screenshot", "When logged: Upload widget never loads")
    ]
    kinds = [e["kind"] for e in client.get(f"/bugs/{bug['id']}/evidence").json()]
    assert kinds[0] == "screenshot"
    assert {"console_error", "failed_request", "navigation"} <= set(kinds)
    # and the Bugs page list carries the screenshot too
    listed = client.get(f"/sessions/{session['id']}/bugs").json()[0]
    assert listed["evidence"][0]["kind"] == "screenshot"


def test_a_bug_without_the_browser_is_unchanged(client, session):
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Typo on the landing page"}).json()
    assert bug["evidence"] == []


# ---------- stopping ----------

def test_stop_saves_trace_and_network_log(client, session, browser):
    status = client.post(f"/sessions/{session['id']}/runner/stop").json()
    assert status["running"] is False
    files = {e["kind"]: e for e in evidence(client, session, "trace,har")}
    assert set(files) == {"trace", "har"}

    trace = client.get(f"/evidence/{files['trace']['id']}/file")
    assert trace.content[:2] == b"PK"  # a zip; open it at trace.playwright.dev
    assert "attachment" in trace.headers["content-disposition"]
    har = client.get(f"/evidence/{files['har']['id']}/file").json()
    assert any(e["response"]["status"] == 500 for e in har["log"]["entries"])

    assert client.post(f"/sessions/{session['id']}/runner/stop").status_code == 409


def test_ending_the_session_closes_the_browser_and_keeps_the_evidence(client, session, browser):
    client.post(f"/sessions/{session['id']}/end")
    assert client.get(f"/sessions/{session['id']}/runner").json()["running"] is False
    kinds = {e["kind"] for e in evidence(client, session)}
    assert {"trace", "har", "console_error", "failed_request"} <= kinds
    again = client.post(f"/sessions/{session['id']}/runner", json={"url": "http://127.0.0.1:9"})
    assert again.status_code == 409


# ---------- guard rails ----------

def test_one_browser_per_session(client, session, site, browser):
    assert client.post(f"/sessions/{session['id']}/runner", json={"url": site}).status_code == 409


def test_screenshot_needs_a_running_browser(client, session):
    assert client.post(f"/sessions/{session['id']}/runner/screenshot", json={}).status_code == 409


def test_a_missing_browser_is_a_clear_503(client, session, monkeypatch):
    monkeypatch.setattr(settings, "runner_chromium_path", "/nonexistent/chromium")
    r = client.post(f"/sessions/{session['id']}/runner", json={"url": "http://127.0.0.1:9"})
    assert r.status_code == 503
    assert "python -m playwright install chromium" in r.json()["detail"]
    assert client.get(f"/sessions/{session['id']}/runner").json()["running"] is False


@pytest.mark.parametrize("url, ok", [("staging.addisverify.et", True), ("ftp://files.example.com", False), ("", False)])
def test_start_url_validation(client, session, url, ok, monkeypatch):
    # Validation happens before any browser starts; a valid URL reaches start().
    from app.runner import RunnerUnavailable

    def start(*args, **kwargs):
        raise RunnerUnavailable("reached start")
    monkeypatch.setattr("app.routers.runner.manager.start", start)
    r = client.post(f"/sessions/{session['id']}/runner", json={"url": url})
    assert r.status_code == (503 if ok else 422)


def test_evidence_files_outside_the_evidence_folder_are_never_served(client, session):
    from app.database import SessionLocal
    with SessionLocal() as db:
        row = models.Evidence(session_id=session["id"], kind="screenshot", summary="x", file_path="/etc/passwd")
        db.add(row)
        db.commit()
        evidence_id = row.id
    assert client.get(f"/evidence/{evidence_id}/file").status_code == 404
